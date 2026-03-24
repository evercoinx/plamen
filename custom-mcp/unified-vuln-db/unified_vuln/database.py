"""
Unified Vulnerability Database v2.0 - Graph-Enhanced Engine

Key upgrades:
- Code-aware embeddings (Nomic/Voyage with Matryoshka support)
- Graph-lite layer for relationship traversal
- Structured JSON output for Claude Code
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Callable
import chromadb
from chromadb.config import Settings
from rich.console import Console

from .schema import Vulnerability, Source

console = Console()

# ═══════════════════════════════════════════════════════════════════════════════
# PATHS - Resolved relative to the Plamen repo root
# ═══════════════════════════════════════════════════════════════════════════════

# Resolve repo root from this file's location:
#   database.py is at: <repo>/custom-mcp/unified-vuln-db/unified_vuln/database.py
#   parents: [0]=unified_vuln, [1]=unified-vuln-db, [2]=custom-mcp, [3]=repo root
_REPO_ROOT = Path(os.environ.get(
    "PLAMEN_HOME",
    str(Path(__file__).resolve().parents[3])
))
DATA_DIR = _REPO_ROOT / "unified-vuln-db" / "data"
CHROMA_DIR = DATA_DIR / "chroma_db"
COLLECTION_NAME = "vulnerabilities_v2"


# ═══════════════════════════════════════════════════════════════════════════════
# EMBEDDING FUNCTIONS - Code-Aware with Matryoshka Support
# ═══════════════════════════════════════════════════════════════════════════════

class CodeAwareEmbeddingFunction:
    """
    Code-aware embedding function with Matryoshka dimension support.
    
    Priority:
    1. Voyage Code 2 (best for code, requires API key)
    2. Nomic Embed Text v1.5 (good local alternative with Matryoshka)
    3. CodeBERT (fallback)
    4. all-MiniLM-L6-v2 (last resort)
    """
    
    def __init__(
        self, 
        model_name: str = "auto",
        dimensions: int = 768,  # Matryoshka: can be 64, 128, 256, 512, 768
        voyage_api_key: Optional[str] = None,
    ):
        self.model_name = model_name
        self.dimensions = dimensions
        self.voyage_api_key = voyage_api_key or os.environ.get("VOYAGE_API_KEY")
        self.model = None
        self.model_type = None
        self._model_name_str = "code-aware-embeddings"
        
        self._initialize()
    
    def name(self) -> str:
        """Return embedding function name (required by ChromaDB)."""
        return self._model_name_str
    
    def _initialize(self):
        """Initialize the best available model."""
        import os
        
        # Check for fast mode (skip heavy models, use MiniLM)
        fast_mode = os.environ.get("VULN_DB_FAST_MODE", "").lower() in ("1", "true", "yes")
        if fast_mode:
            console.print("[yellow]Fast mode enabled - using lightweight model[/yellow]")
            self.model_name = "minilm"
        
        # Try Voyage Code 2 (best for code)
        if self.voyage_api_key and self.model_name in ["auto", "voyage"]:
            try:
                import voyageai
                self.model = voyageai.Client(api_key=self.voyage_api_key)
                self.model_type = "voyage"
                self._model_name_str = "voyage-code-2"
                console.print("[green]Using Voyage Code 2 embeddings (API)[/green]")
                return
            except ImportError:
                console.print("[yellow]voyageai not installed, trying alternatives...[/yellow]")
        
        # Try MiniLM first if not auto (fastest loading, ~90MB)
        if self.model_name == "minilm":
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
                self.model_type = "minilm"
                self._model_name_str = "all-MiniLM-L6-v2"
                self.dimensions = 384  # MiniLM output dimension
                console.print("[green]Using all-MiniLM-L6-v2 (fast)[/green]")
                return
            except Exception as e:
                console.print(f"[yellow]MiniLM failed: {e}[/yellow]")
        
        # Try Nomic Embed (local, Matryoshka support, ~500MB)
        if self.model_name in ["auto", "nomic"]:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(
                    "nomic-ai/nomic-embed-text-v1.5",
                    trust_remote_code=True
                )
                self.model_type = "nomic"
                self._model_name_str = "nomic-embed-v1.5"
                console.print(f"[green]Using Nomic Embed v1.5 (local, dim={self.dimensions})[/green]")
                return
            except Exception as e:
                console.print(f"[yellow]Nomic failed: {e}, trying MiniLM...[/yellow]")
        
        # Fallback to MiniLM (smallest, fastest)
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer("all-MiniLM-L6-v2")
            self.model_type = "minilm"
            self._model_name_str = "all-MiniLM-L6-v2"
            self.dimensions = 384
            console.print("[yellow]Using all-MiniLM-L6-v2 (fallback)[/yellow]")
        except ImportError:
            console.print("[red]No embedding model available![/red]")
            self.model = None
            self.model_type = None
            self._model_name_str = "none"
    
    def __call__(self, input: List[str]) -> List[List[float]]:
        """Generate embeddings for input texts (ChromaDB interface)."""
        return self._embed(input)
    
    def embed_documents(self, input: List[str]) -> List[List[float]]:
        """Embed documents (ChromaDB interface)."""
        return self._embed(input)
    
    def embed_query(self, input: List[str]) -> List[List[float]]:
        """Embed query (ChromaDB interface)."""
        return self._embed(input)
    
    def _embed(self, input: List[str]) -> List[List[float]]:
        """Internal embedding function."""
        if self.model is None:
            return [[0.0] * self.dimensions for _ in input]
        
        if self.model_type == "voyage":
            # Voyage API
            result = self.model.embed(
                input, 
                model="voyage-code-2",
                input_type="document"
            )
            return result.embeddings
        
        elif self.model_type == "nomic":
            # Nomic with Matryoshka dimension truncation
            embeddings = self.model.encode(
                input, 
                show_progress_bar=False,
                convert_to_numpy=True
            )
            # Truncate to desired dimensions (Matryoshka)
            return embeddings[:, :self.dimensions].tolist()
        
        else:
            # Standard sentence-transformers
            embeddings = self.model.encode(input, show_progress_bar=False)
            return embeddings.tolist()


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH-LITE LAYER
# ═══════════════════════════════════════════════════════════════════════════════

class GraphLiteLayer:
    """
    Lightweight graph traversal using metadata links.
    No heavy graph DB required - uses ChromaDB metadata for edges.
    
    Node types:
    - vulnerability:id
    - auditor:name
    - audit_firm:name
    - pattern:name (e.g., pattern:cei-violation)
    - cwe:CWE-XXX
    - library:name
    - protocol:name
    """
    
    def __init__(self, collection):
        self.collection = collection
    
    def find_related(
        self, 
        node_id: str, 
        relation_type: Optional[str] = None,
        max_depth: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Find vulnerabilities related to a node.
        
        Args:
            node_id: Node identifier (e.g., "auditor:trail-of-bits", "pattern:reentrancy")
            relation_type: Filter by relation type (auditor, pattern, cwe, etc.)
            max_depth: Traversal depth (1 = direct connections only)
            
        Returns:
            List of related vulnerability dicts
        """
        # Parse node type
        if ":" in node_id:
            node_type, node_value = node_id.split(":", 1)
        else:
            node_type = None
            node_value = node_id
        
        # Build query based on node type
        results = []
        
        if node_type == "auditor" or (not node_type and relation_type == "auditor"):
            results = self._query_by_metadata("auditor", node_value)
        
        elif node_type == "audit_firm" or (not node_type and relation_type == "audit_firm"):
            results = self._query_by_metadata("audit_firm", node_value)
        
        elif node_type == "pattern":
            # Search in related_nodes field
            results = self._query_by_related_node(f"pattern:{node_value}")
        
        elif node_type == "cwe":
            results = self._query_by_related_node(f"cwe:{node_value}")
        
        elif node_type == "library":
            results = self._query_by_related_node(f"library:{node_value}")
        
        elif node_type == "protocol":
            results = self._query_by_metadata("protocol_name", node_value)
        
        elif node_type == "category":
            results = self._query_by_metadata("category", node_value)
        
        elif node_type == "vulnerability":
            # Get the vulnerability and its related nodes
            vuln = self._get_vuln(node_value)
            if vuln and max_depth > 0:
                # Find all vulns that share related nodes
                related_nodes = vuln.get("metadata", {}).get("related_nodes", "")
                for node in related_nodes.split(","):
                    if node.strip():
                        sub_results = self.find_related(node.strip(), max_depth=0)
                        results.extend(sub_results)
        
        else:
            # Try all metadata fields
            for field in ["auditor", "audit_firm", "protocol_name", "category"]:
                sub_results = self._query_by_metadata(field, node_value)
                results.extend(sub_results)
        
        # Deduplicate
        seen_ids = set()
        unique_results = []
        for r in results:
            rid = r.get("id") or r.get("metadata", {}).get("id")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                unique_results.append(r)
        
        return unique_results
    
    def _query_by_metadata(self, field: str, value: str) -> List[Dict]:
        """Query by exact metadata match."""
        try:
            results = self.collection.get(
                where={field: {"$eq": value}},
                include=["documents", "metadatas"]
            )
            return self._format_results(results)
        except:
            return []
    
    def _query_by_related_node(self, node: str) -> List[Dict]:
        """Query by related_nodes containing a value."""
        try:
            results = self.collection.get(
                where={"related_nodes": {"$contains": node}},
                include=["documents", "metadatas"]
            )
            return self._format_results(results)
        except:
            # ChromaDB might not support $contains, fallback to get all and filter
            return self._filter_by_related_node(node)
    
    def _filter_by_related_node(self, node: str) -> List[Dict]:
        """Fallback: Get all and filter by related_nodes."""
        try:
            all_results = self.collection.get(include=["metadatas"])
            filtered = []
            
            for i, meta in enumerate(all_results.get("metadatas", [])):
                related = meta.get("related_nodes", "")
                if node in related:
                    filtered.append({
                        "id": all_results["ids"][i],
                        "metadata": meta
                    })
            
            return filtered
        except:
            return []
    
    def _get_vuln(self, vuln_id: str) -> Optional[Dict]:
        """Get a single vulnerability by ID."""
        try:
            results = self.collection.get(
                ids=[vuln_id],
                include=["documents", "metadatas"]
            )
            if results["ids"]:
                return {
                    "id": results["ids"][0],
                    "document": results["documents"][0] if results.get("documents") else "",
                    "metadata": results["metadatas"][0] if results.get("metadatas") else {},
                }
        except:
            pass
        return None
    
    def _format_results(self, results: Dict) -> List[Dict]:
        """Format ChromaDB results to standard dicts."""
        formatted = []
        if results and results.get("ids"):
            for i, id in enumerate(results["ids"]):
                formatted.append({
                    "id": id,
                    "document": results.get("documents", [""])[i] if results.get("documents") else "",
                    "metadata": results.get("metadatas", [{}])[i] if results.get("metadatas") else {},
                })
        return formatted
    
    def get_graph_statistics(self) -> Dict[str, Any]:
        """Get statistics about the graph structure."""
        all_data = self.collection.get(include=["metadatas"])
        
        stats = {
            "total_nodes": len(all_data.get("ids", [])),
            "auditors": set(),
            "audit_firms": set(),
            "patterns": set(),
            "protocols": set(),
            "cwes": set(),
        }
        
        for meta in all_data.get("metadatas", []):
            if meta.get("auditor"):
                stats["auditors"].add(meta["auditor"])
            if meta.get("audit_firm"):
                stats["audit_firms"].add(meta["audit_firm"])
            if meta.get("protocol_name"):
                stats["protocols"].add(meta["protocol_name"])
            
            # Parse related_nodes
            for node in meta.get("related_nodes", "").split(","):
                node = node.strip()
                if node.startswith("pattern:"):
                    stats["patterns"].add(node.split(":")[1])
                elif node.startswith("cwe:"):
                    stats["cwes"].add(node.split(":")[1])
        
        # Convert sets to counts
        return {
            "total_vulnerabilities": stats["total_nodes"],
            "unique_auditors": len(stats["auditors"]),
            "unique_audit_firms": len(stats["audit_firms"]),
            "unique_patterns": len(stats["patterns"]),
            "unique_protocols": len(stats["protocols"]),
            "unique_cwes": len(stats["cwes"]),
            "top_auditors": list(stats["auditors"])[:10],
            "top_patterns": list(stats["patterns"])[:10],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DATABASE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class VulnerabilityDB:
    """
    Unified vulnerability database with graph-enhanced search.
    
    Features:
    - Code-aware embeddings (Nomic/Voyage)
    - Matryoshka dimension support
    - Graph-lite relationship traversal
    - Structured JSON outputs for Claude Code
    """
    
    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        embedding_model: str = "auto",
        embedding_dimensions: int = 768,
        voyage_api_key: Optional[str] = None,
    ):
        self.persist_dir = persist_dir or CHROMA_DIR
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # Initialize embedding function FIRST (needed for dimension check)
        self.embedding_fn = CodeAwareEmbeddingFunction(
            model_name=embedding_model,
            dimensions=embedding_dimensions,
            voyage_api_key=voyage_api_key,
        )

        # Check for dimension mismatch with existing DB before opening.
        # A stale DB from a crashed build with a different model (e.g., Nomic 768-dim
        # vs MiniLM 384-dim) causes ChromaDB to hang on get_or_create_collection.
        self._wipe_if_dimension_mismatch()

        # Initialize ChromaDB
        console.print("[dim]Initializing ChromaDB...[/dim]")
        self.client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False)
        )

        # Get or create collection
        console.print("[dim]Opening collection...[/dim]")
        self.collection = self._get_or_create_collection()
        console.print("[dim]Database ready.[/dim]")

        # Initialize graph layer
        self.graph = GraphLiteLayer(self.collection)

    def _wipe_if_dimension_mismatch(self):
        """Detect and wipe a stale ChromaDB whose embedding dimensions don't match the current model.

        ChromaDB's HNSW index is built for a fixed dimension. Opening an existing collection
        with a different-dimension embedding function can hang or silently corrupt. This checks
        the stored dimension metadata and wipes the DB if it doesn't match.
        """
        import sqlite3
        db_file = self.persist_dir / "chroma.sqlite3"
        if not db_file.exists():
            return  # No existing DB — nothing to check

        try:
            conn = sqlite3.connect(str(db_file), timeout=5)
            cursor = conn.execute(
                "SELECT str_value FROM collection_metadata "
                "WHERE key = 'hnsw:space' LIMIT 1"
            )
            row = cursor.fetchone()
            if not row:
                conn.close()
                return  # No collection metadata — let ChromaDB handle creation

            # Check dimension from HNSW segment metadata
            cursor = conn.execute(
                "SELECT int_value FROM segment_metadata "
                "WHERE key = 'hnsw:dimension' LIMIT 1"
            )
            dim_row = cursor.fetchone()
            conn.close()

            if not dim_row:
                return  # No dimension stored yet — fresh or pre-insert collection

            stored_dim = dim_row[0]
            current_dim = self.embedding_fn.dimensions

            if stored_dim != current_dim:
                import shutil
                console.print(
                    f"[yellow]Embedding dimension mismatch: DB has {stored_dim}-dim, "
                    f"current model produces {current_dim}-dim. Wiping stale DB...[/yellow]"
                )
                shutil.rmtree(str(self.persist_dir), ignore_errors=True)
                self.persist_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            # SQLite locked, corrupt, or schema differs — wipe to be safe
            import shutil
            console.print(f"[yellow]Cannot read existing DB ({e}), wiping for clean start...[/yellow]")
            shutil.rmtree(str(self.persist_dir), ignore_errors=True)
            self.persist_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_or_create_collection(self):
        """Get existing collection or create new one."""
        try:
            # Try to get existing collection
            return self.client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={
                    "description": "Unified Web3 vulnerability database v2",
                    "hnsw:space": "cosine",  # Use cosine similarity
                },
                embedding_function=self.embedding_fn
            )
        except Exception as e:
            console.print(f"[yellow]Collection issue: {e}, trying alternative...[/yellow]")
            # Fallback: try without embedding function for existing collections
            try:
                collection = self.client.get_collection(name=COLLECTION_NAME)
                return collection
            except:
                # Create new collection
                return self.client.create_collection(
                    name=COLLECTION_NAME,
                    metadata={
                        "description": "Unified Web3 vulnerability database v2",
                        "hnsw:space": "cosine",
                    },
                    embedding_function=self.embedding_fn
                )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # WRITE OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def add_vulnerability(self, vuln: Vulnerability) -> bool:
        """Add a single vulnerability."""
        try:
            self.collection.add(
                documents=[vuln.to_document()],
                metadatas=[vuln.to_metadata()],
                ids=[vuln.id]
            )
            return True
        except Exception as e:
            if "already exists" in str(e).lower():
                return False
            console.print(f"[red]Error adding {vuln.id}: {e}[/red]")
            return False
    
    def add_vulnerabilities(self, vulns: List[Vulnerability], batch_size: int = 100) -> int:
        """Add multiple vulnerabilities in batches with progress."""
        from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
        
        added = 0
        total_batches = (len(vulns) + batch_size - 1) // batch_size
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Embedding & indexing...", total=len(vulns))
            
            for i in range(0, len(vulns), batch_size):
                batch = vulns[i:i + batch_size]
                
                documents = [v.to_document() for v in batch]
                metadatas = [v.to_metadata() for v in batch]
                ids = [v.id for v in batch]
                
                try:
                    self.collection.add(
                        documents=documents,
                        metadatas=metadatas,
                        ids=ids
                    )
                    added += len(batch)
                except Exception as e:
                    # Add one by one to handle duplicates
                    for v in batch:
                        if self.add_vulnerability(v):
                            added += 1
                
                progress.update(task, advance=len(batch))
        
        return added
    
    def update_vulnerability(self, vuln: Vulnerability) -> bool:
        """Update an existing vulnerability."""
        try:
            self.collection.update(
                documents=[vuln.to_document()],
                metadatas=[vuln.to_metadata()],
                ids=[vuln.id]
            )
            return True
        except Exception as e:
            console.print(f"[red]Error updating {vuln.id}: {e}[/red]")
            return False
    
    def delete_by_source(self, source: str) -> int:
        """Delete all vulnerabilities from a specific source."""
        try:
            results = self.collection.get(
                where={"source": source},
                include=[]
            )
            if results["ids"]:
                self.collection.delete(ids=results["ids"])
                return len(results["ids"])
        except:
            pass
        return 0
    
    # ═══════════════════════════════════════════════════════════════════════════
    # SEARCH OPERATIONS - Return structured JSON for Claude Code
    # ═══════════════════════════════════════════════════════════════════════════
    
    def search(
        self,
        query: str,
        n_results: int = 10,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search with structured JSON output.
        
        Args:
            query: Search query (natural language or code)
            n_results: Number of results
            filters: Dict of metadata filters
                - sources: List[str]
                - categories: List[str]
                - severities: List[str]
                - protocol_types: List[str]
                - has_poc: bool
                - has_diff: bool
                - min_cvss: float
                - auditor: str
                - audit_firm: str
                
        Returns:
            List of vulnerability dicts (for programmatic access)
        """
        # Build where clause
        where = self._build_where_clause(filters) if filters else None
        
        # Execute query
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"]
        )
        
        # Format as structured JSON
        return self._format_search_results(results)
    
    def query_vulnerabilities(
        self,
        filter_dict: Dict[str, Any],
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Programmatic filter access (no semantic search).
        
        Args:
            filter_dict: Metadata filters
            limit: Max results
            offset: Skip first N results
            
        Returns:
            List of vulnerability dicts
        """
        where = self._build_where_clause(filter_dict)
        
        try:
            results = self.collection.get(
                where=where,
                include=["documents", "metadatas"],
                limit=limit,
                offset=offset,
            )
            return self._format_get_results(results)
        except Exception as e:
            console.print(f"[red]Query error: {e}[/red]")
            return []
    
    def get_by_id(self, vuln_id: str) -> Optional[Dict[str, Any]]:
        """Get full vulnerability by ID."""
        try:
            results = self.collection.get(
                ids=[vuln_id],
                include=["documents", "metadatas"]
            )
            
            if results and results["ids"]:
                formatted = self._format_get_results(results)
                return formatted[0] if formatted else None
        except:
            pass
        return None
    
    def get_poc_code(self, vuln_id: str) -> Optional[str]:
        """Get ONLY the PoC code (for piping to file)."""
        vuln = self.get_by_id(vuln_id)
        if vuln and vuln.get("has_poc"):
            return vuln.get("poc_code", "")
        return None
    
    def get_fix_diff(self, vuln_id: str) -> Optional[str]:
        """Get ONLY the fix diff (for patching)."""
        vuln = self.get_by_id(vuln_id)
        if vuln:
            return vuln.get("diff_patch", "")
        return None
    
    def get_vulnerable_code(self, vuln_id: str) -> Optional[str]:
        """Get the vulnerable code snippet."""
        vuln = self.get_by_id(vuln_id)
        if vuln:
            return vuln.get("vulnerable_code", "")
        return None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # GRAPH OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def find_related(
        self, 
        node_id: str,
        relation_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find vulnerabilities related to a node via graph traversal.
        
        Args:
            node_id: Node ID (e.g., "auditor:trail-of-bits", "pattern:reentrancy")
            relation_type: Optional filter
            
        Returns:
            List of related vulnerability dicts
        """
        return self.graph.find_related(node_id, relation_type)
    
    def find_similar_ast(self, ast_signature: str, n_results: int = 10) -> List[Dict[str, Any]]:
        """Find vulnerabilities with similar AST patterns."""
        return self.search(
            query=f"AST Pattern: {ast_signature}",
            n_results=n_results,
        )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive database statistics."""
        all_data = self.collection.get(include=["metadatas"])
        
        stats = {
            "total": len(all_data["ids"]) if all_data["ids"] else 0,
            "by_source": {},
            "by_category": {},
            "by_severity": {},
            "by_protocol_type": {},
            "with_poc": 0,
            "with_diff": 0,
            "by_ast_pattern": {},
        }
        
        if all_data["metadatas"]:
            for meta in all_data["metadatas"]:
                # Count by source
                source = meta.get("source", "unknown")
                stats["by_source"][source] = stats["by_source"].get(source, 0) + 1
                
                # Count by category
                category = meta.get("category", "other")
                stats["by_category"][category] = stats["by_category"].get(category, 0) + 1
                
                # Count by severity
                severity = meta.get("severity", "unknown")
                stats["by_severity"][severity] = stats["by_severity"].get(severity, 0) + 1
                
                # Count by protocol type
                ptype = meta.get("protocol_type", "other")
                stats["by_protocol_type"][ptype] = stats["by_protocol_type"].get(ptype, 0) + 1
                
                # Count with PoC/Diff
                if meta.get("has_poc"):
                    stats["with_poc"] += 1
                if meta.get("has_diff"):
                    stats["with_diff"] += 1
                
                # Count by AST pattern
                ast = meta.get("ast_signature", "")
                if "PATTERN:" in ast:
                    pattern = ast.split("PATTERN:")[1].split()[0] if "PATTERN:" in ast else ""
                    if pattern:
                        stats["by_ast_pattern"][pattern] = stats["by_ast_pattern"].get(pattern, 0) + 1
        
        # Add graph statistics
        stats["graph"] = self.graph.get_graph_statistics()
        
        return stats
    
    # ═══════════════════════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _build_where_clause(self, filters: Dict[str, Any]) -> Optional[Dict]:
        """Build ChromaDB where clause from filter dict."""
        conditions = []
        
        if filters.get("sources"):
            conditions.append({"source": {"$in": filters["sources"]}})
        
        if filters.get("categories"):
            conditions.append({"category": {"$in": filters["categories"]}})
        
        if filters.get("severities"):
            conditions.append({"severity": {"$in": filters["severities"]}})
        
        if filters.get("protocol_types"):
            conditions.append({"protocol_type": {"$in": filters["protocol_types"]}})
        
        if filters.get("has_poc") is not None:
            conditions.append({"has_poc": filters["has_poc"]})
        
        if filters.get("has_diff") is not None:
            conditions.append({"has_diff": filters["has_diff"]})
        
        if filters.get("min_cvss"):
            conditions.append({"cvss_score": {"$gte": filters["min_cvss"]}})
        
        if filters.get("auditor"):
            conditions.append({"auditor": filters["auditor"]})
        
        if filters.get("audit_firm"):
            conditions.append({"audit_firm": filters["audit_firm"]})
        
        if filters.get("protocol_name"):
            conditions.append({"protocol_name": filters["protocol_name"]})
        
        if len(conditions) == 0:
            return None
        elif len(conditions) == 1:
            return conditions[0]
        else:
            return {"$and": conditions}
    
    def _format_search_results(self, results: Dict) -> List[Dict[str, Any]]:
        """Format search results to structured JSON."""
        formatted = []
        
        if results and results.get("ids") and results["ids"][0]:
            for i, id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                doc = results["documents"][0][i] if results.get("documents") else ""
                dist = results["distances"][0][i] if results.get("distances") else 0
                
                formatted.append({
                    "id": id,
                    "score": 1 - dist,  # Convert distance to similarity
                    **meta,
                    "document": doc,
                })
        
        return formatted
    
    def _format_get_results(self, results: Dict) -> List[Dict[str, Any]]:
        """Format get results to structured JSON."""
        formatted = []
        
        if results and results.get("ids"):
            for i, id in enumerate(results["ids"]):
                meta = results["metadatas"][i] if results.get("metadatas") else {}
                doc = results["documents"][i] if results.get("documents") else ""
                
                formatted.append({
                    "id": id,
                    **meta,
                    "document": doc,
                })
        
        return formatted
    
    def clear(self):
        """Clear the entire database."""
        try:
            self.client.delete_collection(COLLECTION_NAME)
        except:
            pass
        self.collection = self._get_or_create_collection()


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_db_instance: Optional[VulnerabilityDB] = None


def get_db(
    embedding_model: str = "auto",
    embedding_dimensions: int = 768,
) -> VulnerabilityDB:
    """Get the database singleton."""
    global _db_instance
    if _db_instance is None:
        _db_instance = VulnerabilityDB(
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
        )
    return _db_instance
