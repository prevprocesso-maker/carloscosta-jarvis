from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from abc import ABC, abstractmethod

from .paths import get_jarvis_paths
from ..core.telemetry import trace, get_telemetry


class DocumentType(Enum):
    TEXT = "text"
    PDF = "pdf"
    MARKDOWN = "markdown"
    HTML = "html"
    CODE = "code"
    JSON = "json"
    YAML = "yaml"


class ChunkStrategy(Enum):
    FIXED_SIZE = "fixed_size"
    SEMANTIC = "semantic"
    RECURSIVE = "recursive"


@dataclass
class Document:
    id: str
    title: str
    content: str
    doc_type: DocumentType
    source: str  # file path, url, etc.
    metadata: Dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    tags: List[str] = field(default_factory=list)
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]


@dataclass
class Chunk:
    id: str
    document_id: str
    content: str
    index: int
    start_char: int
    end_char: int
    embedding: Optional[List[float]] = None
    metadata: Dict = field(default_factory=dict)
    token_count: int = 0


@dataclass
class SearchResult:
    chunk: Chunk
    document: Document
    score: float
    snippet: str


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        pass

    @abstractmethod
    async def embed_query(self, query: str) -> List[float]:
        pass


class MockEmbeddingProvider(EmbeddingProvider):
    """Provider mock deterministico para testes sem API externa.
    Usa hash do texto para gerar embeddings consistentes."""
    def __init__(self, dim: int = 384):
        self.dim = dim

    def _text_to_embedding(self, text: str) -> List[float]:
        import hashlib
        import struct
        hash_bytes = hashlib.md5(text.encode('utf-8')).digest()
        result = []
        for i in range(0, len(hash_bytes), 4):
            if len(result) >= self.dim:
                break
            chunk = hash_bytes[i:i+4]
            if len(chunk) == 4:
                val = struct.unpack('<f', chunk)[0]
                result.append(max(-1.0, min(1.0, val)))
        while len(result) < self.dim:
            result.append(0.0)
        return result[:self.dim]

    async def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._text_to_embedding(t) for t in texts]

    async def embed_query(self, query: str) -> List[float]:
        return self._text_to_embedding(query)


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """Provider usando sentence-transformers (ex: BGE, MiniLM, E5).
    Suporta modelos multilingue e portugues."""
    
    _MODELOS_RECOMENDADOS = {
        "multilingue": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "portugues": "neuralmind/bert-base-portuguese-cased",
        "bge_multilingue": "BAAI/bge-m3",
        "bge_small": "BAAI/bge-small-en-v1.5",
        "e5_multilingue": "intfloat/multilingual-e5-large",
        "mxbai": "mixedbread-ai/mxbai-embed-large-v1",
    }
    
    def __init__(
        self, 
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        device: str = "auto",
        batch_size: int = 32,
        normalize: bool = True,
    ):
        try:
            from sentence_transformers import SentenceTransformer
            import torch
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers nao instalado. "
                "Instale: pip install sentence-transformers torch"
            ) from e
        
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        
        self.model = SentenceTransformer(model_name, device=device)
        self.batch_size = batch_size
        self.normalize = normalize
        self.dim = self.model.get_sentence_embedding_dimension()
        print(f"[Embedding] Modelo: {model_name} | Device: {device} | Dim: {self.dim}")
    
    async def embed(self, texts: List[str]) -> List[List[float]]:
        import asyncio
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: self.model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=self.normalize,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        )
        return embeddings.tolist()
    
    async def embed_query(self, query: str) -> List[float]:
        import asyncio
        loop = asyncio.get_event_loop()
        emb = await loop.run_in_executor(
            None,
            lambda: self.model.encode(
                [query],
                normalize_embeddings=self.normalize,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        )
        return emb[0].tolist()


class SQLiteVectorStore:
    def __init__(self, db_path: Path, embedding_dim: int = 384):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_dim = embedding_dim
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    doc_type TEXT NOT NULL,
                    source TEXT,
                    metadata TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    tags TEXT,
                    hash TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    index_num INTEGER,
                    start_char INTEGER,
                    end_char INTEGER,
                    embedding BLOB,
                    metadata TEXT,
                    token_count INTEGER,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                )
            """)
            # FTS5 table for full-text search (keyword/BM25)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    content, document_id, chunk_id,
                    content='chunks', content_rowid='rowid'
                )
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunks_fts(rowid, content, document_id, chunk_id) VALUES (new.rowid, new.content, new.document_id, new.id);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, content, document_id, chunk_id) VALUES ('delete', old.rowid, old.content, old.document_id, old.id);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, content, document_id, chunk_id) VALUES ('delete', old.rowid, old.content, old.document_id, old.id);
                    INSERT INTO chunks_fts(rowid, content, document_id, chunk_id) VALUES (new.rowid, new.content, new.document_id, new.id);
                END
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON chunks(embedding)")

    def add_document(self, doc: Document):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO documents (id, title, content, doc_type, source, metadata, created_at, updated_at, tags, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc.id, doc.title, doc.content, doc.doc_type.value,
                doc.source, json.dumps(doc.metadata),
                doc.created_at.isoformat(), doc.updated_at.isoformat(),
                json.dumps(doc.tags), doc.hash
            ))

    def add_chunk(self, chunk: Chunk):
        with sqlite3.connect(self.db_path) as conn:
            emb_bytes = None
            if chunk.embedding:
                emb_bytes = json.dumps(chunk.embedding).encode()
            conn.execute("""
                INSERT OR REPLACE INTO chunks (id, document_id, content, index_num, start_char, end_char, embedding, metadata, token_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                chunk.id, chunk.document_id, chunk.content, chunk.index,
                chunk.start_char, chunk.end_char, emb_bytes,
                json.dumps(chunk.metadata), chunk.token_count
            ))
            # Also insert into FTS5 table
            conn.execute("""
                INSERT INTO chunks_fts(rowid, content, document_id, chunk_id)
                SELECT rowid, ?, ?, ? FROM chunks WHERE id = ?
            """, (chunk.content, chunk.document_id, chunk.id, chunk.id))

    def get_document(self, doc_id: str) -> Optional[Document]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if row:
                return Document(
                    id=row["id"], title=row["title"], content=row["content"],
                    doc_type=DocumentType(row["doc_type"]), source=row["source"],
                    metadata=json.loads(row["metadata"] or "{}"),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                    tags=json.loads(row["tags"] or "[]"),
                    hash=row["hash"],
                )
        return None

    def get_chunks(self, doc_id: str) -> List[Chunk]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM chunks WHERE document_id = ? ORDER BY index_num", (doc_id,)).fetchall()
            return [
                Chunk(
                    id=row["id"], document_id=row["document_id"], content=row["content"],
                    index=row["index_num"], start_char=row["start_char"],
                    end_char=row["end_char"], embedding=json.loads(row["embedding"]) if row["embedding"] else None,
                    metadata=json.loads(row["metadata"] or "{}"),
                    token_count=row["token_count"],
                )
                for row in rows
            ]

    def delete_document(self, doc_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def search_similar(self, query_embedding: List[float], limit: int = 10, threshold: float = 0.7) -> List[Tuple[Chunk, float]]:
        """Busca por similaridade de cosseno (aproximada via SQLite)."""
        # Para producao, use pgvector, pinecone, chromadb, etc.
        # Aqui implementacao simplificada via scan
        query_vec = query_embedding
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM chunks WHERE embedding IS NOT NULL").fetchall()

        results = []
        for row in rows:
            chunk = Chunk(
                id=row["id"], document_id=row["document_id"], content=row["content"],
                index=row["index_num"], start_char=row["start_char"],
                end_char=row["end_char"], embedding=json.loads(row["embedding"]),
                metadata=json.loads(row["metadata"] or "{}"),
                token_count=row["token_count"],
            )
            if chunk.embedding:
                score = self._cosine_similarity(query_vec, chunk.embedding)
                if score >= threshold:
                    results.append((chunk, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)



    def search_keyword(self, query: str, limit: int = 10) -> List[Tuple[Chunk, float]]:
        """Busca por palavras-chave usando SQLite FTS5 (BM25)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # FTS5 BM25 ranking
            rows = conn.execute("""
                SELECT c.*, bm25(chunks_fts) as bm25_score
                FROM chunks_fts fts
                JOIN chunks c ON c.rowid = fts.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY bm25_score
                LIMIT ?
            """, (query, limit)).fetchall()

        results = []
        for row in rows:
            chunk = Chunk(
                id=row["id"], document_id=row["document_id"], content=row["content"],
                index=row["index_num"], start_char=row["start_char"],
                end_char=row["end_char"], embedding=json.loads(row["embedding"]) if row["embedding"] else None,
                metadata=json.loads(row["metadata"] or "{}"),
                token_count=row["token_count"],
            )
            # BM25 score (lower is better), convert to similarity-like score
            bm25 = row["bm25_score"]
            score = 1.0 / (1.0 + bm25) if bm25 > 0 else 1.0
            results.append((chunk, score))

        return results


class Chunker:
    def __init__(self, strategy: ChunkStrategy = ChunkStrategy.FIXED_SIZE, chunk_size: int = 512, overlap: int = 50):
        self.strategy = strategy
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_text(self, text: str, doc_id: str, metadata: Dict = None) -> List[Chunk]:
        if self.strategy == ChunkStrategy.FIXED_SIZE:
            return self._fixed_size_chunk(text, doc_id, metadata)
        elif self.strategy == ChunkStrategy.RECURSIVE:
            return self._recursive_chunk(text, doc_id, metadata)
        else:
            return self._fixed_size_chunk(text, doc_id, metadata)

    def _fixed_size_chunk(self, text: str, doc_id: str, metadata: Dict = None) -> List[Chunk]:
        chunks = []
        chars = list(text)
        start = 0
        index = 0
        while start < len(chars):
            end = min(start + self.chunk_size, len(chars))
            chunk_text = "".join(chars[start:end])
            chunk = Chunk(
                id=str(uuid.uuid4())[:16],
                document_id=doc_id,
                content=chunk_text,
                index=len(chunks),
                start_char=start,
                end_char=end,
                metadata=metadata or {},
                token_count=len(chunk_text.split()),
            )
            chunks.append(chunk)
            start += self.chunk_size - self.overlap
        return chunks

    def _recursive_chunk(self, text: str, doc_id: str, metadata: Dict = None) -> List[Chunk]:
        separators = ["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " "]
        return self._recursive_split(text, separators, doc_id, metadata)

    def _recursive_split(self, text: str, separators: List[str], doc_id: str, metadata: Dict) -> List[Chunk]:
        if len(text) <= self.chunk_size:
            return [Chunk(
                id=str(uuid.uuid4())[:16],
                document_id=doc_id,
                content=text,
                index=0,
                start_char=0,
                end_char=len(text),
                metadata=metadata,
                token_count=len(text.split()),
            )]

        for sep in separators:
            parts = text.split(sep)
            if len(parts) > 1:
                chunks = []
                current = ""
                for part in parts:
                    if len(current) + len(part) + len(sep) <= self.chunk_size:
                        current += part + sep
                    else:
                        if current:
                            chunks.extend(self._recursive_split(current.rstrip(sep), separators[1:], doc_id, metadata))
                        current = part + sep
                if current:
                    chunks.extend(self._recursive_split(current.rstrip(sep), separators[1:], doc_id, metadata))
                return chunks

        # Fallback
        return self._fixed_size_chunk(text, doc_id, metadata)



        # Fallback
        return self._fixed_size_chunk(text, doc_id, metadata)


class CrossEncoderReranker:
    """Cross-encoder reranker para melhorar qualidade da busca.
    Usa modelos como bge-reranker-base, bge-reranker-large, etc."""
    
    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        device: str = "auto",
        batch_size: int = 16,
        max_length: int = 512,
    ):
        try:
            from sentence_transformers import CrossEncoder
            import torch
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers nao instalado para CrossEncoder. "
                "Instale: pip install sentence-transformers torch"
            ) from e
        
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        
        self.model = CrossEncoder(model_name, device=device, max_length=max_length)
        self.batch_size = batch_size
        self.device = device
        print(f"[Reranker] Modelo: {model_name} | Device: {device}")
    
    def rerank(self, query: str, documents: List[str], top_k: int = None) -> List[Tuple[int, float]]:
        """Reordena documentos por relev├óncia para a query.
        Retorna lista de (index_original, score) ordenada por score desc."""
        if not documents:
            return []
        
        pairs = [(query, doc) for doc in documents]
        scores = self.model.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)
        
        # Ordena por score decrescente
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        if top_k:
            ranked = ranked[:top_k]
        return ranked


class KnowledgeBase:
    def __init__(
        self,
        store_path: Path = None,
        embedding_provider: EmbeddingProvider = None,
        chunk_strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
        chunk_size: int = 512,
        overlap: int = 50,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_device: str = "auto",
    ):
        paths = get_jarvis_paths()
        self.store_path = store_path or paths.data / "knowledge" / "vectors.db"
        self.store = SQLiteVectorStore(self.store_path)
        
        # Provider de embedding: tenta SentenceTransformer, cai para Mock se falhar
        if embedding_provider is not None:
            self.embedding_provider = embedding_provider
        else:
            try:
                self.embedding_provider = SentenceTransformerEmbeddingProvider(
                    model_name=embedding_model,
                    device=embedding_device,
                )
                print(f"[KnowledgeBase] Usando SentenceTransformer: {embedding_model}")
            except Exception as e:
                print(f"[KnowledgeBase] Falha ao carregar SentenceTransformer ({e}), usando MockEmbeddingProvider")
                self.embedding_provider = MockEmbeddingProvider()
        
        self.chunker = Chunker(chunk_strategy, chunk_size, overlap)
        self._initialized = False
        self._reranker: Optional[CrossEncoderReranker] = None
        self._reranker_model = "BAAI/bge-reranker-base"

    def _get_reranker(self) -> CrossEncoderReranker:
        """Lazy load cross-encoder reranker."""
        if self._reranker is None:
            try:
                self._reranker = CrossEncoderReranker(
                    model_name=self._reranker_model,
                    device="auto",
                    batch_size=16,
                )
            except Exception as e:
                print(f"[KnowledgeBase] Falha ao carregar reranker ({e}), sem reranking")
                self._reranker = None
        return self._reranker

    async def initialize(self):
        if self._initialized:
            return
        # Warm up embedding provider
        await self.embedding_provider.embed(["test"])
        self._initialized = True

    @trace("knowledge.add_document")
    async def add_document(
        self,
        title: str,
        content: str,
        doc_type: DocumentType = DocumentType.TEXT,
        source: str = "",
        metadata: Dict = None,
        tags: List[str] = None,
    ) -> Document:
        await self.initialize()

        doc = Document(
            id=str(uuid.uuid4())[:16],
            title=title,
            content=content,
            doc_type=doc_type,
            source=source,
            metadata=metadata or {},
            tags=tags or [],
        )

        # Chunk
        chunks = self.chunker.chunk_text(content, doc.id, metadata)

        # Embed chunks
        if chunks:
            texts = [c.content for c in chunks]
            embeddings = await self.embedding_provider.embed(texts)
            for chunk, emb in zip(chunks, embeddings):
                chunk.embedding = emb

        # Save
        self.store.add_document(doc)
        for chunk in chunks:
            self.store.add_chunk(chunk)

        return doc

    @trace("knowledge.add_file")
    async def add_file(self, file_path: Path, metadata: Dict = None, tags: List[str] = None) -> Document:
        """Adiciona arquivo ├á base de conhecimento."""
        if not file_path.exists():
            raise FileNotFoundError(file_path)

        # Detect type
        suffix = file_path.suffix.lower()
        doc_type_map = {
            ".txt": DocumentType.TEXT,
            ".md": DocumentType.MARKDOWN,
            ".py": DocumentType.CODE,
            ".js": DocumentType.CODE,
            ".ts": DocumentType.CODE,
            ".json": DocumentType.JSON,
            ".yaml": DocumentType.YAML,
            ".yml": DocumentType.YAML,
            ".html": DocumentType.HTML,
        }
        doc_type = doc_type_map.get(suffix, DocumentType.TEXT)

        content = file_path.read_text(encoding="utf-8", errors="ignore")
        return await self.add_document(
            title=file_path.stem,
            content=content,
            doc_type=doc_type,
            source=str(file_path),
            metadata={**(metadata or {}), "file_path": str(file_path), "file_size": file_path.stat().st_size},
            tags=tags,
        )

    @trace("knowledge.add_directory")
    async def add_directory(self, dir_path: Path, pattern: str = "**/*", metadata: Dict = None, tags: List[str] = None) -> List[Document]:
        """Adiciona todos arquivos de um diretorio."""
        docs = []
        for file_path in dir_path.glob(pattern):
            if file_path.is_file():
                try:
                    doc = await self.add_file(file_path, metadata, tags)
                    docs.append(doc)
                except Exception as e:
                    print(f"[KB] Error adding {file_path}: {e}")
        return docs

    @trace("knowledge.search")
    async def search(self, query: str, limit: int = 10, threshold: float = 0.25) -> List[SearchResult]:
        await self.initialize()
        query_emb = await self.embedding_provider.embed_query(query)
        chunk_scores = self.store.search_similar(query_emb, limit * 2, threshold)

        results = []
        for chunk, score in chunk_scores[:limit]:
            doc = self.store.get_document(chunk.document_id)
            if doc:
                snippet = chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content
                results.append(SearchResult(chunk=chunk, document=doc, score=score, snippet=snippet))
        return results

    @trace("knowledge.search_hybrid")
    async def hybrid_search(
        self,
        query: str,
        limit: int = 10,
        alpha: float = 0.5,
        rerank: bool = True,
        rerank_top_k: int = 20,
    ) -> List[SearchResult]:
        """Busca hibrida completa: Vector + BM25 (FTS5) + RRF + Cross-encoder Rerank.
        
        Args:
            query: Query de busca
            limit: N├║mero final de resultados
            alpha: Peso do vector search (1-alpha = peso keyword). 0.5 = igual.
            rerank: Se deve usar cross-encoder reranker
            rerank_top_k: Quantos candidatos passar pro reranker
        """
        await self.initialize()
        
        # 1. Vector Search (semantic)
        query_emb = await self.embedding_provider.embed_query(query)
        vector_chunks = self.store.search_similar(query_emb, limit * 3, 0.0)
        
        # 2. Keyword Search (FTS5 BM25)
        keyword_chunks = self.store.search_keyword(query, limit * 3)
        
        # 3. Reciprocal Rank Fusion (RRF)
        k = 60  # RRF constant
        fused_scores = {}
        
        # Add vector ranks
        for rank, (chunk, score) in enumerate(vector_chunks):
            fused_scores[chunk.id] = fused_scores.get(chunk.id, 0) + 1.0 / (k + rank + 1)
        
        # Add keyword ranks
        for rank, (chunk, score) in enumerate(keyword_chunks):
            fused_scores[chunk.id] = fused_scores.get(chunk.id, 0) + 1.0 / (k + rank + 1)
        
        # Sort by fused score
        rerank_candidates = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)[:rerank_top_k]
        
        if not rerank_candidates:
            return []
        
        # Get full chunk objects for reranking
        candidate_chunks = []
        chunk_map = {}
        for chunk_id, _ in rerank_candidates:
            # Find in vector results first
            chunk = next((c for c, _ in vector_chunks if c.id == chunk_id), None)
            if chunk is None:
                chunk = next((c for c, _ in keyword_chunks if c.id == chunk_id), None)
            if chunk:
                candidate_chunks.append(chunk)
                chunk_map[chunk.id] = chunk
        
        # 4. Cross-encoder Reranking
        if rerank and self._get_reranker() is not None and len(candidate_chunks) > 1:
            reranker = self._get_reranker()
            texts = [c.content for c in candidate_chunks]
            ranked = reranker.rerank(query, texts, top_k=limit)
            
            # Build final results with reranked order
            final_results = []
            for orig_idx, rerank_score in ranked:
                chunk = candidate_chunks[orig_idx]
                doc = self.store.get_document(chunk.document_id)
                if doc:
                    snippet = chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content
                    final_results.append(SearchResult(
                        chunk=chunk, document=doc, score=rerank_score, snippet=snippet
                    ))
            return final_results
        
        # Fallback: return RRF ranked without reranking
        final_results = []
        for chunk_id, fused_score in rerank_candidates[:limit]:
            chunk = chunk_map.get(chunk_id)
            if chunk:
                doc = self.store.get_document(chunk.document_id)
                if doc:
                    snippet = chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content
                    final_results.append(SearchResult(
                        chunk=chunk, document=doc, score=fused_score, snippet=snippet
                    ))
        return final_results

    @trace("knowledge.ask")
    async def ask(self, question: str, context_limit: int = 5) -> Dict:
        """RAG: Retrieval-Augmented Generation."""
        results = await self.search(question, limit=context_limit)

        context = "\n\n".join([
            f"[Fonte: {r.document.title}]\n{r.snippet}"
            for r in results
        ])

        return {
            "question": question,
            "context": context,
            "sources": [
                {
                    "document_id": r.document.id,
                    "title": r.document.title,
                    "score": r.score,
                    "snippet": r.snippet,
                }
                for r in results
            ],
            "context_used": len(results),
        }

    def get_document(self, doc_id: str) -> Optional[Document]:
        return self.store.get_document(doc_id)

    def list_documents(self, limit: int = 100, offset: int = 0) -> List[Document]:
        with sqlite3.connect(self.store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM documents ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
            return [
                Document(
                    id=r["id"], title=r["title"], content=r["content"],
                    doc_type=DocumentType(r["doc_type"]), source=r["source"],
                    metadata=json.loads(r["metadata"] or "{}"),
                    created_at=datetime.fromisoformat(r["created_at"]),
                    updated_at=datetime.fromisoformat(r["updated_at"]),
                    tags=json.loads(r["tags"] or "[]"),
                    hash=r["hash"],
                )
                for r in rows
            ]

    def delete_document(self, doc_id: str):
        self.store.delete_document(doc_id)

    def get_stats(self) -> Dict:
        with sqlite3.connect(self.store.db_path) as conn:
            doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            return {
                "documents": doc_count,
                "chunks": chunk_count,
                "store_path": str(self.store.db_path),
            }


# Global knowledge base
_kb: Optional[KnowledgeBase] = None


def get_knowledge_base(
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    embedding_device: str = "auto",
) -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase(embedding_model=embedding_model, embedding_device=embedding_device)
    return _kb


def init_knowledge_base(
    store_path: Path = None,
    embedding_provider: EmbeddingProvider = None,
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    embedding_device: str = "auto",
    **kwargs
) -> KnowledgeBase:
    global _kb
    _kb = KnowledgeBase(
        store_path, 
        embedding_provider, 
        embedding_model=embedding_model,
        embedding_device=embedding_device,
        **kwargs
    )
    return _kb
