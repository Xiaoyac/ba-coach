"""Disposable, independent SQLite FTS5 BM25 indexes, one per KB category.

The authoritative corpus remains MySQL. This in-memory index contains source
passages only and is rebuilt from the same revision as the dense index.
"""
import sqlite3


class SparseIndex:
    def __init__(self, members, token_rows):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.execute("CREATE VIRTUAL TABLE passages USING fts5(content)")
        self.connection.executemany("INSERT INTO passages(rowid, content) VALUES (?, ?)",
                                   [(i, " ".join(tokens)) for i, tokens in zip(members, token_rows)])
        self.connection.commit()

    def search(self, terms, limit):
        if not terms:
            return []
        # Match literal terms joined by OR, never execute user FTS expressions.
        query = " OR ".join('"' + term.replace('"', '""') + '"' for term in dict.fromkeys(terms))
        rows = self.connection.execute(
            "SELECT rowid, bm25(passages) AS score FROM passages "
            "WHERE passages MATCH ? ORDER BY score, rowid LIMIT ?", (query, limit)).fetchall()
        return [(i, -float(score)) for i, score in rows]
