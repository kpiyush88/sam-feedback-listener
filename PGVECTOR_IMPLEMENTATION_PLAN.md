# pgvector Implementation Plan for SAM Feedback Topic Clustering

## Executive Summary

This document outlines the comprehensive plan to implement pgvector-based topic clustering in Supabase for analyzing SAM feedback messages. The system will use OpenAI embeddings to generate vector representations of user queries and agent responses, then apply multiple clustering algorithms (K-means, DBSCAN, Hierarchical) to discover conversation topics and patterns in real-time.

**Key Goals:**
- Enable semantic search and similarity matching of conversations
- Automatically cluster user queries to understand common topics
- Cluster agent responses to analyze answer patterns
- Cluster query-response pairs to understand interaction patterns
- Support real-time cluster assignment as new messages arrive
- Compare multiple clustering approaches to find the optimal solution

---

## Architecture Overview

```
┌─────────────────┐
│  Solace Broker  │
└────────┬────────┘
         │ Messages
         ▼
┌─────────────────────────┐
│  sam_listener.py        │
│  (Message Handler)      │
└────────┬────────────────┘
         │ Parsed Messages
         ▼
┌─────────────────────────┐
│  supabase_uploader.py   │
│  + Embedding Generator  │
└────────┬────────────────┘
         │ Store + Embed
         ▼
┌─────────────────────────┐
│  Supabase (PostgreSQL)  │
│  + pgvector Extension   │
│                         │
│  Tables:                │
│  - conversations        │
│  - tasks                │
│  - messages + embeddings│
│  - topic_clusters       │
│  - message_clusters     │
└────────┬────────────────┘
         │ Query/Analyze
         ▼
┌─────────────────────────┐
│  cluster_analyzer.py    │
│  (K-means, DBSCAN, etc) │
└─────────────────────────┘
```

---

## Task Breakdown

### Phase 1: Database Setup

#### Task 1.1: Enable pgvector Extension in Supabase
**Objective:** Activate the pgvector extension to enable vector operations in PostgreSQL.

**Steps:**
1. Log into Supabase dashboard
2. Navigate to SQL Editor
3. Run the SQL command to enable pgvector:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
4. Verify extension is enabled:
   ```sql
   SELECT * FROM pg_extension WHERE extname = 'vector';
   ```

**Why:** pgvector adds vector data types and similarity search functions to PostgreSQL, enabling efficient storage and querying of embeddings.

**Deliverable:** Confirmed pgvector extension enabled in Supabase instance.

---

#### Task 1.2: Create Database Schema for Embeddings and Clusters
**Objective:** Design and implement the database schema to store embeddings, clusters, and their relationships.

**Schema Design:**

**1. Add embedding column to existing `messages` table:**
```sql
ALTER TABLE messages
ADD COLUMN embedding vector(1536),
ADD COLUMN embedding_model varchar(100),
ADD COLUMN embedding_generated_at timestamptz;

-- Create index for vector similarity search
CREATE INDEX ON messages
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

**2. Create `embeddings` table (flexible storage):**
```sql
CREATE TABLE embeddings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type varchar(50) NOT NULL, -- 'user_query', 'agent_response', 'query_response_pair'
  entity_id uuid NOT NULL, -- Reference to message_id or conversation_id
  content text NOT NULL, -- The actual text that was embedded
  embedding vector(1536) NOT NULL,
  embedding_model varchar(100) NOT NULL,
  metadata jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX ON embeddings (entity_type);
CREATE INDEX ON embeddings (entity_id);
```

**3. Create `topic_clusters` table:**
```sql
CREATE TABLE topic_clusters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cluster_id integer NOT NULL,
  cluster_name varchar(255),
  cluster_description text,
  algorithm varchar(50) NOT NULL, -- 'kmeans', 'dbscan', 'hierarchical'
  entity_type varchar(50) NOT NULL, -- 'user_query', 'agent_response', 'query_response_pair'
  centroid vector(1536), -- For K-means
  sample_texts text[], -- Representative examples from this cluster
  num_members integer DEFAULT 0,
  metadata jsonb, -- Store algorithm params, metrics, etc.
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),

  UNIQUE(algorithm, entity_type, cluster_id)
);

CREATE INDEX ON topic_clusters (algorithm, entity_type);
```

**4. Create `message_clusters` table (junction table):**
```sql
CREATE TABLE message_clusters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  embedding_id uuid REFERENCES embeddings(id) ON DELETE CASCADE,
  cluster_id uuid REFERENCES topic_clusters(id) ON DELETE CASCADE,
  distance_to_centroid float, -- How close to cluster center
  confidence_score float, -- Clustering confidence
  assigned_at timestamptz DEFAULT now(),

  UNIQUE(embedding_id, cluster_id)
);

CREATE INDEX ON message_clusters (embedding_id);
CREATE INDEX ON message_clusters (cluster_id);
```

**5. Create `cluster_analysis` table:**
```sql
CREATE TABLE cluster_analysis (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  algorithm varchar(50) NOT NULL,
  entity_type varchar(50) NOT NULL,
  num_clusters integer,
  silhouette_score float, -- Clustering quality metric
  davies_bouldin_index float, -- Lower is better
  calinski_harabasz_score float, -- Higher is better
  inertia float, -- For K-means
  parameters jsonb, -- Algorithm-specific params
  total_samples integer,
  run_at timestamptz DEFAULT now(),

  metadata jsonb
);

CREATE INDEX ON cluster_analysis (algorithm, entity_type, run_at DESC);
```

**Why:** This schema supports:
- Multiple embedding types (queries, responses, pairs)
- Multiple clustering algorithms running in parallel
- Historical tracking of cluster quality over time
- Efficient similarity search with pgvector indexes

**Deliverable:** SQL migration file `sql/001_pgvector_schema.sql` with all table definitions and indexes.

---

#### Task 1.3: Create Database Functions for Similarity Search
**Objective:** Create PostgreSQL functions to simplify common vector operations.

**Functions to Create:**

**1. Find similar messages:**
```sql
CREATE OR REPLACE FUNCTION find_similar_messages(
  query_embedding vector(1536),
  match_threshold float DEFAULT 0.8,
  match_count int DEFAULT 10
)
RETURNS TABLE (
  message_id uuid,
  content text,
  similarity float,
  agent_name varchar,
  timestamp timestamptz
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    m.id,
    m.content,
    1 - (m.embedding <=> query_embedding) as similarity,
    m.agent_name,
    m.timestamp
  FROM messages m
  WHERE m.embedding IS NOT NULL
    AND 1 - (m.embedding <=> query_embedding) > match_threshold
  ORDER BY m.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
```

**2. Find nearest cluster:**
```sql
CREATE OR REPLACE FUNCTION find_nearest_cluster(
  query_embedding vector(1536),
  algorithm_name varchar,
  entity_type_name varchar
)
RETURNS TABLE (
  cluster_id uuid,
  cluster_name varchar,
  distance float
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    tc.id,
    tc.cluster_name,
    tc.centroid <=> query_embedding as distance
  FROM topic_clusters tc
  WHERE tc.algorithm = algorithm_name
    AND tc.entity_type = entity_type_name
    AND tc.centroid IS NOT NULL
  ORDER BY tc.centroid <=> query_embedding
  LIMIT 1;
END;
$$;
```

**3. Get cluster statistics:**
```sql
CREATE OR REPLACE FUNCTION get_cluster_stats(
  algorithm_name varchar,
  entity_type_name varchar
)
RETURNS TABLE (
  cluster_id integer,
  cluster_name varchar,
  member_count bigint,
  avg_distance float,
  sample_texts text[]
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    tc.cluster_id,
    tc.cluster_name,
    COUNT(mc.id)::bigint as member_count,
    AVG(mc.distance_to_centroid) as avg_distance,
    tc.sample_texts
  FROM topic_clusters tc
  LEFT JOIN message_clusters mc ON tc.id = mc.cluster_id
  WHERE tc.algorithm = algorithm_name
    AND tc.entity_type = entity_type_name
  GROUP BY tc.id, tc.cluster_id, tc.cluster_name, tc.sample_texts
  ORDER BY member_count DESC;
END;
$$;
```

**Why:** These functions encapsulate common operations and make the application code cleaner. They leverage pgvector's optimized distance operators.

**Deliverable:** SQL file `sql/002_vector_functions.sql` with all function definitions.

---

### Phase 2: Embedding Generation

#### Task 2.1: Create OpenAI Embedding Generator Module
**Objective:** Build a Python module to generate embeddings using OpenAI's API.

**File:** `embedding_generator.py`

**Key Components:**

**1. EmbeddingGenerator Class:**
```python
class EmbeddingGenerator:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        """
        Initialize with OpenAI API key
        model options:
          - text-embedding-3-small (1536 dims, cheaper)
          - text-embedding-3-large (3072 dims, higher quality)
        """

    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for a single text"""

    def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts (batch processing)"""

    def estimate_cost(self, num_tokens: int) -> float:
        """Estimate API cost for given token count"""
```

**2. Text Preparation Functions:**
```python
def prepare_user_query(parsed_message: ParsedMessage) -> str:
    """
    Extract and clean user query text
    - Remove system timestamps
    - Normalize whitespace
    - Handle multi-part messages
    """

def prepare_agent_response(parsed_message: ParsedMessage) -> str:
    """
    Extract agent response text
    - Combine multi-part responses
    - Remove artifact references
    - Clean formatting
    """

def prepare_query_response_pair(query: str, response: str) -> str:
    """
    Combine query and response into single text for pair embedding
    Format: "Query: {query}\n\nResponse: {response}"
    """
```

**3. Embedding Cache:**
```python
class EmbeddingCache:
    """
    Cache embeddings to avoid regenerating for identical text
    - Use hash of text as key
    - Store in local SQLite or Redis
    - TTL of 30 days
    """

    def get(self, text: str) -> Optional[List[float]]:
        """Get cached embedding if exists"""

    def set(self, text: str, embedding: List[float]):
        """Cache embedding for text"""
```

**Features:**
- Automatic retry logic with exponential backoff
- Rate limiting to respect OpenAI API limits
- Token counting for cost estimation
- Error handling for API failures
- Support for both models (small and large)
- Batch processing for efficiency

**Why:** OpenAI embeddings provide high-quality semantic representations. The text-embedding-3-small model is cost-effective and produces 1536-dimensional vectors suitable for most clustering tasks.

**Deliverable:** `embedding_generator.py` with complete implementation and unit tests.

---

#### Task 2.2: Integrate Embedding Generation into Message Upload Pipeline
**Objective:** Modify the existing upload pipeline to generate and store embeddings in real-time.

**File to Modify:** `supabase_uploader.py`

**Changes:**

**1. Add embedding generation to `_insert_message` method:**
```python
def _insert_message(self, parsed: ParsedMessage, conversation_uuid: str, task_uuid: str) -> str:
    # ... existing message insertion code ...

    # Generate embeddings for different entity types
    embeddings_to_generate = []

    # For user queries
    if parsed.role == MessageRole.USER:
        query_text = self.message_parser.extract_query(parsed)
        if query_text:
            embeddings_to_generate.append({
                'entity_type': 'user_query',
                'content': query_text
            })

    # For agent responses
    if parsed.role == MessageRole.AGENT:
        response_text = self.message_parser.extract_response(parsed)
        if response_text:
            embeddings_to_generate.append({
                'entity_type': 'agent_response',
                'content': response_text
            })

    # Generate and store embeddings
    for emb_config in embeddings_to_generate:
        self._generate_and_store_embedding(
            message_uuid,
            emb_config['entity_type'],
            emb_config['content']
        )

    return message_uuid
```

**2. Add new method `_generate_and_store_embedding`:**
```python
def _generate_and_store_embedding(
    self,
    entity_id: str,
    entity_type: str,
    content: str
):
    """
    Generate embedding and store in database
    Also assign to nearest cluster if clusters exist
    """
    try:
        # Generate embedding
        embedding = self.embedding_generator.generate_embedding(content)

        # Store in embeddings table
        embedding_data = {
            'entity_type': entity_type,
            'entity_id': entity_id,
            'content': content,
            'embedding': embedding,
            'embedding_model': self.embedding_generator.model,
            'metadata': {}
        }

        response = self.client.table('embeddings').insert(embedding_data).execute()
        embedding_uuid = response.data[0]['id']

        # Find and assign to nearest cluster if exists
        self._assign_to_nearest_cluster(embedding_uuid, embedding, entity_type)

        return embedding_uuid

    except Exception as e:
        print(f"Error generating embedding: {e}")
        return None
```

**3. Add cluster assignment method:**
```python
def _assign_to_nearest_cluster(
    self,
    embedding_id: str,
    embedding: List[float],
    entity_type: str
):
    """
    Find nearest cluster and assign embedding to it
    Uses the most recent K-means clustering by default
    """
    # Query for nearest cluster using SQL function
    result = self.client.rpc(
        'find_nearest_cluster',
        {
            'query_embedding': embedding,
            'algorithm_name': 'kmeans',
            'entity_type_name': entity_type
        }
    ).execute()

    if result.data:
        cluster = result.data[0]

        # Insert into message_clusters
        self.client.table('message_clusters').insert({
            'embedding_id': embedding_id,
            'cluster_id': cluster['cluster_id'],
            'distance_to_centroid': cluster['distance'],
            'confidence_score': 1.0 / (1.0 + cluster['distance'])
        }).execute()
```

**4. Handle query-response pairs:**
```python
def _generate_pair_embedding(self, conversation_uuid: str):
    """
    After both query and response are received, generate pair embedding
    Called when agent response is received
    """
    # Get latest user query and agent response from conversation
    messages = self.client.table('messages')\
        .select('*')\
        .eq('conversation_id', conversation_uuid)\
        .order('timestamp', desc=True)\
        .limit(10)\
        .execute()

    # Find the most recent user-agent pair
    query = None
    response = None

    for msg in messages.data:
        if msg['role'] == 'agent' and response is None:
            response = msg['content']
        elif msg['role'] == 'user' and query is None:
            query = msg['content']

        if query and response:
            break

    if query and response:
        pair_text = f"Query: {query}\n\nResponse: {response}"
        self._generate_and_store_embedding(
            conversation_uuid,
            'query_response_pair',
            pair_text
        )
```

**Why:** Real-time embedding generation ensures that all messages are immediately searchable and can be assigned to clusters. This enables live topic tracking and analysis.

**Deliverable:** Updated `supabase_uploader.py` with embedding generation integrated.

---

### Phase 3: Clustering Implementation

#### Task 3.1: Create Cluster Analyzer Module with Multiple Algorithms
**Objective:** Implement K-means, DBSCAN, and Hierarchical clustering algorithms for topic discovery.

**File:** `cluster_analyzer.py`

**Key Components:**

**1. Base Cluster Analyzer:**
```python
class ClusterAnalyzer:
    """Base class for clustering algorithms"""

    def __init__(self, supabase_client: Client):
        self.client = supabase_client

    def load_embeddings(self, entity_type: str) -> Tuple[np.ndarray, List[Dict]]:
        """
        Load all embeddings of a specific type from database
        Returns: (embeddings_matrix, metadata_list)
        """

    def evaluate_clustering(
        self,
        embeddings: np.ndarray,
        labels: np.ndarray
    ) -> Dict[str, float]:
        """
        Calculate clustering quality metrics:
        - Silhouette score ([-1, 1], higher is better)
        - Davies-Bouldin index (≥0, lower is better)
        - Calinski-Harabasz score (higher is better)
        """

    def find_optimal_clusters(
        self,
        embeddings: np.ndarray,
        min_clusters: int = 2,
        max_clusters: int = 20
    ) -> int:
        """
        Use elbow method and silhouette analysis to find optimal K
        """

    def save_clusters_to_db(
        self,
        clusters: List[Dict],
        algorithm: str,
        entity_type: str,
        metrics: Dict
    ):
        """
        Save cluster definitions and assignments to database
        """
```

**2. K-means Clustering:**
```python
class KMeansClusterAnalyzer(ClusterAnalyzer):
    """K-means clustering implementation"""

    def cluster(
        self,
        entity_type: str,
        n_clusters: int = None,
        auto_find_optimal: bool = True
    ) -> Dict[str, Any]:
        """
        Perform K-means clustering

        Steps:
        1. Load embeddings from database
        2. Find optimal K if not specified
        3. Run K-means clustering
        4. Calculate cluster centroids
        5. Extract representative samples
        6. Evaluate clustering quality
        7. Save results to database

        Returns: Clustering results with metrics
        """

    def _extract_cluster_topics(
        self,
        cluster_id: int,
        member_embeddings: List[Dict]
    ) -> Dict:
        """
        Analyze cluster members to extract:
        - Representative sample texts
        - Common keywords (using TF-IDF)
        - Suggested cluster name
        """
```

**3. DBSCAN Clustering:**
```python
class DBSCANClusterAnalyzer(ClusterAnalyzer):
    """Density-based clustering (DBSCAN)"""

    def cluster(
        self,
        entity_type: str,
        eps: float = None,
        min_samples: int = 5,
        auto_tune: bool = True
    ) -> Dict[str, Any]:
        """
        Perform DBSCAN clustering

        Advantages:
        - Automatically determines number of clusters
        - Can find arbitrarily shaped clusters
        - Identifies outliers (noise points)

        Parameters:
        - eps: Maximum distance between points in same cluster
        - min_samples: Minimum points to form dense region
        """

    def _tune_parameters(
        self,
        embeddings: np.ndarray
    ) -> Tuple[float, int]:
        """
        Use k-distance graph to find optimal eps
        Use heuristic for min_samples (2 * dimensions)
        """
```

**4. Hierarchical Clustering:**
```python
class HierarchicalClusterAnalyzer(ClusterAnalyzer):
    """Agglomerative hierarchical clustering"""

    def cluster(
        self,
        entity_type: str,
        n_clusters: int = None,
        linkage: str = 'ward',
        distance_threshold: float = None
    ) -> Dict[str, Any]:
        """
        Perform hierarchical clustering

        Advantages:
        - Creates dendrogram showing cluster relationships
        - Can cut tree at different levels
        - No need to specify K upfront

        Linkage methods:
        - ward: Minimize variance within clusters
        - average: Average distance between all pairs
        - complete: Maximum distance between pairs
        """

    def generate_dendrogram(self) -> str:
        """
        Generate and save dendrogram visualization
        Returns path to saved image
        """
```

**5. Cluster Comparison:**
```python
class ClusterComparison:
    """Compare results from different clustering algorithms"""

    def compare_algorithms(
        self,
        entity_type: str
    ) -> pd.DataFrame:
        """
        Run all algorithms and compare:
        - Number of clusters found
        - Silhouette scores
        - Coverage (% of points clustered)
        - Stability (consistency across runs)

        Returns comparison table
        """

    def recommend_algorithm(
        self,
        comparison_results: pd.DataFrame
    ) -> str:
        """
        Recommend best algorithm based on:
        - Highest silhouette score
        - Good coverage (>90%)
        - Reasonable number of clusters
        """
```

**Why:** Multiple clustering algorithms handle data differently. K-means works well for spherical clusters, DBSCAN handles irregular shapes and outliers, and Hierarchical provides insights into cluster relationships. Comparing them helps find the best approach for your data.

**Deliverable:** `cluster_analyzer.py` with all three algorithms and comparison utilities.

---

#### Task 3.2: Create Cluster Topic Naming and Summarization
**Objective:** Automatically generate meaningful names and descriptions for discovered clusters.

**File:** `cluster_topic_extractor.py`

**Key Components:**

**1. Topic Extraction:**
```python
class TopicExtractor:
    """Extract meaningful topics from cluster members"""

    def __init__(self, openai_api_key: str = None):
        self.openai_client = openai.Client(api_key=openai_api_key) if openai_api_key else None

    def extract_keywords_tfidf(
        self,
        cluster_texts: List[str],
        all_texts: List[str]
    ) -> List[Tuple[str, float]]:
        """
        Use TF-IDF to find most important words in cluster
        Compare against all texts to find distinctive terms

        Returns: List of (keyword, score) tuples
        """

    def generate_cluster_name_gpt(
        self,
        cluster_texts: List[str]
    ) -> str:
        """
        Use GPT to generate a descriptive cluster name

        Prompt: "Given these user queries about a company system,
                 suggest a concise 2-4 word topic name:
                 [sample texts]"
        """

    def generate_cluster_description(
        self,
        cluster_texts: List[str],
        keywords: List[str]
    ) -> str:
        """
        Generate human-readable description of cluster

        Uses GPT with prompt: "Summarize the common theme
                               in these queries in 1-2 sentences"
        """

    def find_representative_samples(
        self,
        cluster_embeddings: np.ndarray,
        cluster_centroid: np.ndarray,
        cluster_texts: List[str],
        n_samples: int = 5
    ) -> List[str]:
        """
        Find most representative texts from cluster
        - Select texts closest to centroid
        - Ensure diversity (not too similar to each other)
        """
```

**2. Topic Labeling Pipeline:**
```python
def label_cluster(
    cluster_id: int,
    cluster_members: List[Dict],
    all_members: List[Dict],
    use_gpt: bool = True
) -> Dict[str, Any]:
    """
    Complete pipeline for labeling a cluster:

    1. Extract texts from members
    2. Compute TF-IDF keywords
    3. Find representative samples
    4. Generate name (GPT or keywords)
    5. Generate description (GPT or template)

    Returns:
    {
        'cluster_name': str,
        'description': str,
        'keywords': List[str],
        'sample_texts': List[str]
    }
    """
```

**Example Output:**
```python
# For a cluster of hotel booking queries:
{
    'cluster_name': 'Hotel Booking & Travel',
    'description': 'Users asking about company procedures for booking hotels and business travel arrangements',
    'keywords': ['hotel', 'booking', 'travel', 'concur', 'expense', 'accommodation'],
    'sample_texts': [
        'what is the best way of booking hotels through the company?',
        'How do I book a hotel for business travel?',
        'What is the company policy on hotel expenses?',
        'Can I use my own hotel booking or must I use Concur?',
        'What hotels are approved for business travel?'
    ]
}
```

**Why:** Raw cluster numbers (Cluster 0, Cluster 1) are not meaningful to humans. Automatic topic extraction and naming makes clusters interpretable and actionable for business insights.

**Deliverable:** `cluster_topic_extractor.py` with keyword extraction and GPT-based naming.

---

### Phase 4: Cluster Management

#### Task 4.1: Create Cluster Manager for Lifecycle Operations
**Objective:** Build a management system for creating, updating, and maintaining clusters over time.

**File:** `cluster_manager.py`

**Key Components:**

**1. ClusterManager Class:**
```python
class ClusterManager:
    """Manages cluster lifecycle and operations"""

    def __init__(self, supabase_client: Client, embedding_generator: EmbeddingGenerator):
        self.client = supabase_client
        self.embedding_gen = embedding_generator
        self.kmeans_analyzer = KMeansClusterAnalyzer(supabase_client)
        self.dbscan_analyzer = DBSCANClusterAnalyzer(supabase_client)
        self.hierarchical_analyzer = HierarchicalClusterAnalyzer(supabase_client)

    def run_full_clustering(
        self,
        entity_type: str,
        algorithms: List[str] = ['kmeans', 'dbscan', 'hierarchical']
    ) -> Dict[str, Any]:
        """
        Run complete clustering pipeline:
        1. Load all embeddings for entity_type
        2. Run specified algorithms
        3. Compare results
        4. Label clusters
        5. Save to database
        6. Generate reports

        Returns: Clustering results and comparison
        """

    def update_clusters_incremental(
        self,
        entity_type: str,
        algorithm: str = 'kmeans'
    ):
        """
        Update existing clusters with new data:
        1. Load existing cluster centroids
        2. Load new embeddings since last clustering
        3. Assign new embeddings to nearest clusters
        4. Check if re-clustering is needed (drift detection)
        5. Re-cluster if necessary
        """

    def detect_cluster_drift(
        self,
        entity_type: str,
        threshold: float = 0.15
    ) -> bool:
        """
        Detect if clusters have drifted significantly:
        - Check average distance of new points to centroids
        - Compare with historical average
        - Return True if drift > threshold
        """

    def merge_similar_clusters(
        self,
        algorithm: str,
        entity_type: str,
        similarity_threshold: float = 0.9
    ):
        """
        Merge clusters that are too similar:
        1. Calculate centroid similarity matrix
        2. Identify pairs with similarity > threshold
        3. Merge clusters
        4. Update assignments
        """

    def split_large_clusters(
        self,
        algorithm: str,
        entity_type: str,
        max_size: int = 100
    ):
        """
        Split clusters that are too large:
        1. Identify clusters with > max_size members
        2. Run sub-clustering (K=2 or 3)
        3. Create new clusters
        4. Update assignments
        """
```

**2. Scheduled Operations:**
```python
class ClusterScheduler:
    """Schedule periodic clustering operations"""

    def schedule_daily_reclustering(
        self,
        entity_types: List[str],
        time: str = "02:00"
    ):
        """
        Schedule daily re-clustering at specified time
        - Runs all algorithms
        - Compares with previous day
        - Sends report if significant changes
        """

    def schedule_hourly_updates(
        self,
        entity_types: List[str]
    ):
        """
        Schedule hourly incremental updates
        - Assigns new embeddings to clusters
        - Checks for drift
        - Triggers re-clustering if needed
        """
```

**3. Cluster Export and Reporting:**
```python
class ClusterReporter:
    """Generate reports and exports of clustering results"""

    def export_clusters_to_csv(
        self,
        algorithm: str,
        entity_type: str,
        output_path: str
    ):
        """
        Export cluster results to CSV:
        - cluster_id, cluster_name, text, distance_to_centroid
        """

    def generate_cluster_summary_report(
        self,
        algorithm: str,
        entity_type: str
    ) -> str:
        """
        Generate markdown report with:
        - Total clusters found
        - Cluster sizes (distribution)
        - Quality metrics
        - Top clusters with samples
        - Visualization (if available)
        """

    def visualize_clusters_2d(
        self,
        algorithm: str,
        entity_type: str,
        output_path: str
    ):
        """
        Create 2D visualization using t-SNE or UMAP:
        - Reduce embeddings to 2D
        - Plot points colored by cluster
        - Label cluster centroids
        - Save as PNG
        """
```

**Why:** Clusters need active management as data evolves. New conversation topics emerge, old topics fade, and cluster boundaries shift. Automated management ensures clusters stay relevant and accurate.

**Deliverable:** `cluster_manager.py` with full lifecycle management.

---

#### Task 4.2: Create Cluster Query and Analysis API
**Objective:** Provide easy-to-use functions for querying and analyzing clusters.

**File:** `cluster_query.py`

**Key Functions:**

**1. Query Operations:**
```python
def get_all_clusters(
    algorithm: str,
    entity_type: str,
    supabase_client: Client
) -> pd.DataFrame:
    """
    Get all clusters with statistics
    Columns: cluster_id, name, description, size, keywords, samples
    """

def get_cluster_members(
    cluster_id: str,
    supabase_client: Client,
    limit: int = 100
) -> List[Dict]:
    """
    Get all messages in a cluster
    Includes: text, timestamp, agent, distance_to_centroid
    """

def find_cluster_for_text(
    text: str,
    entity_type: str,
    algorithm: str,
    embedding_generator: EmbeddingGenerator,
    supabase_client: Client
) -> Dict:
    """
    Given new text, predict which cluster it belongs to:
    1. Generate embedding for text
    2. Find nearest cluster centroid
    3. Return cluster info + confidence
    """

def search_similar_messages(
    query_text: str,
    entity_type: str,
    embedding_generator: EmbeddingGenerator,
    supabase_client: Client,
    limit: int = 10
) -> List[Dict]:
    """
    Semantic search for similar messages:
    1. Generate embedding for query
    2. Use pgvector similarity search
    3. Return top K most similar messages
    """
```

**2. Analysis Operations:**
```python
def analyze_cluster_trends_over_time(
    cluster_id: str,
    supabase_client: Client,
    time_interval: str = 'day'
) -> pd.DataFrame:
    """
    Analyze how cluster membership changes over time:
    - Count of messages per time interval
    - Detect trending topics
    """

def compare_clusters_across_types(
    cluster_id_user_query: str,
    cluster_id_agent_response: str,
    supabase_client: Client
) -> Dict:
    """
    Compare user query cluster with agent response cluster:
    - Find query-response co-occurrence
    - Identify which agent responses address which user topics
    """

def identify_outliers(
    algorithm: str,
    entity_type: str,
    supabase_client: Client,
    threshold: float = 2.0
) -> List[Dict]:
    """
    Find messages that don't fit well in any cluster:
    - Distance to centroid > threshold * std_dev
    - May indicate new emerging topics
    """

def get_cluster_quality_metrics(
    algorithm: str,
    entity_type: str,
    supabase_client: Client
) -> Dict:
    """
    Get latest quality metrics for clustering:
    - Silhouette score
    - Number of clusters
    - Coverage percentage
    - Average cluster size
    """
```

**Why:** These functions make it easy to integrate clustering insights into dashboards, reports, and applications without dealing with low-level database queries.

**Deliverable:** `cluster_query.py` with comprehensive query API.

---

### Phase 5: Integration and Testing

#### Task 5.1: Update Main Listener to Use Real-Time Clustering
**Objective:** Integrate all components into the main message listener.

**File to Modify:** `sam_listener_with_supabase.py`

**Changes:**

**1. Initialize new components:**
```python
# In main() function
embedding_generator = EmbeddingGenerator(
    api_key=os.getenv("OPENAI_API_KEY"),
    model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
)

cluster_manager = ClusterManager(supabase_client, embedding_generator)

# Pass to message handler
message_handler = FeedbackMessageHandlerWithSupabase(
    output_dir=OUTPUT_DIR,
    filter_topics=FILTER_TOPICS,
    enable_supabase=ENABLE_SUPABASE,
    embedding_generator=embedding_generator,
    cluster_manager=cluster_manager
)
```

**2. Update SupabaseUploader initialization:**
```python
# In supabase_uploader.py __init__
def __init__(
    self,
    supabase_url: str = None,
    supabase_key: str = None,
    embedding_generator: EmbeddingGenerator = None
):
    # ... existing code ...
    self.embedding_generator = embedding_generator
```

**3. Add periodic re-clustering trigger:**
```python
# In main()
import threading
import time

def periodic_clustering():
    """Background task to re-cluster periodically"""
    while True:
        time.sleep(3600)  # Every hour
        try:
            cluster_manager.update_clusters_incremental('user_query')
            cluster_manager.update_clusters_incremental('agent_response')
            cluster_manager.update_clusters_incremental('query_response_pair')
            print("✓ Periodic cluster update completed")
        except Exception as e:
            print(f"⚠️ Periodic clustering failed: {e}")

# Start background thread
clustering_thread = threading.Thread(target=periodic_clustering, daemon=True)
clustering_thread.start()
```

**Why:** Seamless integration ensures clustering happens automatically without manual intervention.

**Deliverable:** Updated `sam_listener_with_supabase.py` with clustering integrated.

---

#### Task 5.2: Create Initial Clustering Script
**Objective:** Create a standalone script to run initial clustering on existing data.

**File:** `run_initial_clustering.py`

**Script Structure:**
```python
#!/usr/bin/env python3
"""
Initial clustering script for existing data
Run this once after setting up pgvector to cluster historical messages
"""

import os
from dotenv import load_dotenv
from supabase import create_client
from embedding_generator import EmbeddingGenerator
from cluster_manager import ClusterManager

def main():
    load_dotenv()

    # Initialize clients
    supabase = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_KEY")
    )

    embedding_gen = EmbeddingGenerator(
        api_key=os.getenv("OPENAI_API_KEY")
    )

    cluster_mgr = ClusterManager(supabase, embedding_gen)

    print("="*60)
    print("Initial Clustering Pipeline")
    print("="*60)

    # Step 1: Generate embeddings for existing messages
    print("\n[1/4] Generating embeddings for existing messages...")
    generate_historical_embeddings(supabase, embedding_gen)

    # Step 2: Run clustering for user queries
    print("\n[2/4] Clustering user queries...")
    results_queries = cluster_mgr.run_full_clustering(
        entity_type='user_query',
        algorithms=['kmeans', 'dbscan', 'hierarchical']
    )
    print(f"  Found {results_queries['best_num_clusters']} query clusters")

    # Step 3: Run clustering for agent responses
    print("\n[3/4] Clustering agent responses...")
    results_responses = cluster_mgr.run_full_clustering(
        entity_type='agent_response',
        algorithms=['kmeans', 'dbscan', 'hierarchical']
    )
    print(f"  Found {results_responses['best_num_clusters']} response clusters")

    # Step 4: Run clustering for pairs
    print("\n[4/4] Clustering query-response pairs...")
    results_pairs = cluster_mgr.run_full_clustering(
        entity_type='query_response_pair',
        algorithms=['kmeans', 'dbscan']
    )
    print(f"  Found {results_pairs['best_num_clusters']} pair clusters")

    # Generate reports
    print("\n[Reports] Generating cluster reports...")
    from cluster_manager import ClusterReporter
    reporter = ClusterReporter(supabase)

    reporter.generate_cluster_summary_report('kmeans', 'user_query')
    reporter.generate_cluster_summary_report('kmeans', 'agent_response')

    print("\n" + "="*60)
    print("Initial clustering complete!")
    print("="*60)

def generate_historical_embeddings(supabase, embedding_gen):
    """Generate embeddings for messages that don't have them yet"""

    # Get all messages without embeddings
    messages = supabase.table('messages')\
        .select('*')\
        .is_('embedding', 'null')\
        .execute()

    total = len(messages.data)
    print(f"  Found {total} messages without embeddings")

    # Process in batches
    batch_size = 50
    for i in range(0, total, batch_size):
        batch = messages.data[i:i+batch_size]

        # Generate embeddings
        texts = [msg['content'] for msg in batch]
        embeddings = embedding_gen.generate_embeddings_batch(texts)

        # Update database
        for msg, emb in zip(batch, embeddings):
            supabase.table('messages')\
                .update({
                    'embedding': emb,
                    'embedding_model': embedding_gen.model
                })\
                .eq('id', msg['id'])\
                .execute()

        print(f"  Processed {min(i+batch_size, total)}/{total}")

if __name__ == "__main__":
    main()
```

**Why:** Existing messages need embeddings generated before clustering can work. This script handles the one-time migration.

**Deliverable:** `run_initial_clustering.py` script ready to run.

---

#### Task 5.3: Create Comprehensive Test Suite
**Objective:** Ensure all components work correctly with unit and integration tests.

**File:** `tests/test_clustering.py`

**Test Categories:**

**1. Embedding Tests:**
```python
def test_embedding_generation():
    """Test that embeddings are generated correctly"""

def test_embedding_dimension():
    """Test embedding has correct dimensions (1536)"""

def test_batch_embedding():
    """Test batch generation produces consistent results"""

def test_embedding_cache():
    """Test caching avoids duplicate API calls"""
```

**2. Clustering Tests:**
```python
def test_kmeans_clustering():
    """Test K-means produces valid clusters"""

def test_dbscan_clustering():
    """Test DBSCAN handles outliers correctly"""

def test_hierarchical_clustering():
    """Test hierarchical creates dendrogram"""

def test_optimal_k_selection():
    """Test elbow method finds reasonable K"""

def test_cluster_quality_metrics():
    """Test silhouette score calculation"""
```

**3. Database Tests:**
```python
def test_vector_storage():
    """Test vectors are stored and retrieved correctly"""

def test_similarity_search():
    """Test pgvector similarity search"""

def test_cluster_assignment():
    """Test messages are assigned to nearest cluster"""

def test_cluster_functions():
    """Test SQL functions return expected results"""
```

**4. Integration Tests:**
```python
def test_end_to_end_pipeline():
    """
    Test complete pipeline:
    1. Receive message
    2. Parse
    3. Generate embedding
    4. Assign to cluster
    5. Verify in database
    """

def test_incremental_clustering():
    """Test new messages are assigned correctly"""

def test_cluster_drift_detection():
    """Test drift detection triggers re-clustering"""
```

**Why:** Comprehensive testing ensures reliability and catches issues before production deployment.

**Deliverable:** Complete test suite in `tests/` directory.

---

### Phase 6: Visualization and Reporting

#### Task 6.1: Create Cluster Visualization Dashboard
**Objective:** Build visual tools to explore and understand clusters.

**File:** `cluster_dashboard.py` (Optional: Use Streamlit or Dash)

**Dashboard Features:**

**1. Cluster Overview:**
- Table of all clusters with sizes
- Quality metrics display
- Algorithm comparison chart

**2. 2D Cluster Visualization:**
- t-SNE or UMAP projection
- Interactive scatter plot (Plotly)
- Color-coded by cluster
- Hover to see text

**3. Cluster Details:**
- Click cluster to see members
- Representative samples
- Keyword cloud
- Time series of cluster growth

**4. Similarity Search:**
- Input box to enter query
- Shows similar messages
- Highlights which cluster they belong to

**Example with Streamlit:**
```python
import streamlit as st
import plotly.express as px

st.title("SAM Feedback Topic Clusters")

# Sidebar
algorithm = st.sidebar.selectbox("Algorithm", ["kmeans", "dbscan", "hierarchical"])
entity_type = st.sidebar.selectbox("Entity Type", ["user_query", "agent_response", "query_response_pair"])

# Load data
clusters = get_all_clusters(algorithm, entity_type, supabase_client)

# Display metrics
col1, col2, col3 = st.columns(3)
col1.metric("Total Clusters", len(clusters))
col2.metric("Total Messages", clusters['size'].sum())
col3.metric("Silhouette Score", get_quality_score())

# Cluster table
st.subheader("Clusters")
st.dataframe(clusters)

# Visualization
st.subheader("2D Visualization")
fig = create_cluster_plot(algorithm, entity_type)
st.plotly_chart(fig)

# Search
st.subheader("Semantic Search")
query = st.text_input("Enter search query:")
if query:
    results = search_similar_messages(query, entity_type)
    st.write(results)
```

**Why:** Visual exploration helps validate clusters and discover insights that aren't obvious from raw data.

**Deliverable:** Interactive dashboard (optional but recommended).

---

#### Task 6.2: Create Automated Reports
**Objective:** Generate regular reports on conversation topics and trends.

**File:** `generate_cluster_report.py`

**Report Types:**

**1. Daily Topic Summary:**
```markdown
# SAM Feedback Topic Analysis
Date: 2025-10-20

## User Query Topics
Total queries: 156

### Top 5 Topics
1. **Hotel Booking & Travel** (42 queries, 27%)
   - Sample: "what is the best way of booking hotels through the company?"
   - Trend: ↑ 15% from yesterday

2. **Expense Reimbursement** (31 queries, 20%)
   - Sample: "How do I submit expenses in Concur?"
   - Trend: → Stable

3. **HR Policies** (28 queries, 18%)
   - Sample: "What is the vacation policy?"
   - Trend: ↓ 8% from yesterday

...

## Agent Response Topics
Most common response types:
1. Policy explanations (45%)
2. System instructions (32%)
3. Escalations to human (15%)

## New Emerging Topics
- "Remote work equipment" (8 new queries)
- "Team building budget" (5 new queries)
```

**2. Weekly Trend Report:**
- Topic volume over time
- Emerging topics
- Declining topics
- Agent effectiveness by topic

**3. Monthly Executive Summary:**
- High-level insights
- User pain points
- Recommendation for documentation improvements

**Why:** Regular reports keep stakeholders informed and help identify areas for improvement in the AI system.

**Deliverable:** Automated report generation scripts.

---

### Phase 7: Deployment and Monitoring

#### Task 7.1: Update Dependencies and Environment
**Objective:** Ensure all required packages are installed and configured.

**File to Update:** `requirements.txt`

**New Dependencies:**
```txt
# Existing
supabase>=2.0.0
solace-pubsubplus>=1.6.0
python-dotenv>=1.0.0

# New for embeddings and clustering
openai>=1.0.0
numpy>=1.24.0
scikit-learn>=1.3.0
scipy>=1.11.0
pandas>=2.0.0

# Optional for visualization
matplotlib>=3.7.0
plotly>=5.17.0
seaborn>=0.12.0
streamlit>=1.28.0  # If building dashboard

# Optional for advanced features
umap-learn>=0.5.4  # Better than t-SNE for visualization
nltk>=3.8.1  # For keyword extraction
```

**Environment Variables to Add (`.env`):**
```bash
# Existing
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
SOLACE_HOST=your_solace_host
SOLACE_VPN=your_vpn
SOLACE_USERNAME=your_username
SOLACE_PASSWORD=your_password
SOLACE_TOPIC=your_topic

# New
OPENAI_API_KEY=your_openai_key
EMBEDDING_MODEL=text-embedding-3-small
ENABLE_CLUSTERING=true
CLUSTERING_INTERVAL_HOURS=1
MIN_MESSAGES_FOR_CLUSTERING=50
```

**Deliverable:** Updated `requirements.txt` and `.env.example`.

---

#### Task 7.2: Create Deployment Documentation
**Objective:** Document how to set up and run the system.

**File:** `DEPLOYMENT.md`

**Contents:**

**1. Prerequisites:**
- Python 3.9+
- Supabase account
- OpenAI API key
- Solace broker access

**2. Setup Steps:**
```bash
# 1. Clone repository
git clone <repo>
cd sam-feedback-listener

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 4. Setup database
python -m sql.run_migrations

# 5. Run initial clustering on existing data
python run_initial_clustering.py

# 6. Start listener
python sam_listener_with_supabase.py
```

**3. Monitoring:**
- Check Supabase logs
- Monitor OpenAI API usage
- Review clustering quality metrics

**4. Troubleshooting:**
- Common errors and solutions
- How to re-run clustering
- How to reset clusters

**Deliverable:** Complete deployment documentation.

---

#### Task 7.3: Create Monitoring and Alerting
**Objective:** Set up monitoring for system health and clustering quality.

**File:** `monitoring.py`

**Monitoring Components:**

**1. Health Checks:**
```python
def check_embedding_generation_health():
    """
    Check if embeddings are being generated:
    - % of recent messages with embeddings
    - Average time to generate
    - API error rate
    """

def check_clustering_health():
    """
    Check clustering quality:
    - Silhouette score trend
    - Cluster size distribution
    - Outlier rate
    """

def check_database_health():
    """
    Check database performance:
    - Query response time
    - Index usage
    - Storage size
    """
```

**2. Alerts:**
```python
def alert_if_embedding_failures_high():
    """Alert if >10% of embeddings fail"""

def alert_if_clustering_quality_drops():
    """Alert if silhouette score drops below threshold"""

def alert_if_new_topic_emerges():
    """Alert if large number of outliers detected"""
```

**3. Metrics Dashboard:**
- Embedding generation rate
- Clustering quality over time
- Top topics trending
- API costs

**Why:** Proactive monitoring catches issues before they impact users and helps optimize costs.

**Deliverable:** Monitoring scripts and alerting configuration.

---

## Summary of Deliverables

### SQL Files
- `sql/001_pgvector_schema.sql` - Database schema
- `sql/002_vector_functions.sql` - Utility functions

### Python Modules
- `embedding_generator.py` - OpenAI embedding generation
- `cluster_analyzer.py` - All clustering algorithms
- `cluster_topic_extractor.py` - Topic naming and summarization
- `cluster_manager.py` - Cluster lifecycle management
- `cluster_query.py` - Query and analysis API
- Updated `supabase_uploader.py` - With embedding integration
- Updated `sam_listener_with_supabase.py` - With clustering integration

### Scripts
- `run_initial_clustering.py` - One-time setup script
- `generate_cluster_report.py` - Automated reporting
- `cluster_dashboard.py` - Interactive visualization (optional)
- `monitoring.py` - Health checks and alerts

### Documentation
- `PGVECTOR_IMPLEMENTATION_PLAN.md` - This document
- `DEPLOYMENT.md` - Setup and deployment guide
- `CLUSTER_USAGE.md` - How to use clustering API

### Configuration
- Updated `requirements.txt`
- Updated `.env.example`

---

## Estimated Timeline

| Phase | Tasks | Estimated Time |
|-------|-------|----------------|
| Phase 1: Database Setup | 3 tasks | 4-6 hours |
| Phase 2: Embedding Generation | 2 tasks | 6-8 hours |
| Phase 3: Clustering Implementation | 2 tasks | 10-12 hours |
| Phase 4: Cluster Management | 2 tasks | 8-10 hours |
| Phase 5: Integration & Testing | 3 tasks | 8-10 hours |
| Phase 6: Visualization | 2 tasks | 6-8 hours |
| Phase 7: Deployment | 3 tasks | 4-6 hours |
| **Total** | **17 tasks** | **46-60 hours** |

---

## Next Steps

1. Review and approve this plan
2. Set up OpenAI API account and get API key
3. Begin Phase 1: Database Setup
4. Test with small dataset before full deployment
5. Iterate based on results

---

## Questions to Consider

1. **Budget:** OpenAI embeddings cost ~$0.02 per 1M tokens. Estimate your monthly message volume to calculate costs.

2. **Storage:** pgvector indexes require storage. 1536-dim vectors are ~6KB each. Plan storage accordingly.

3. **Performance:** How often should clusters be updated? Hourly? Daily?

4. **Privacy:** Are there any PII concerns with storing message embeddings?

5. **Access:** Who should have access to cluster insights and reports?
