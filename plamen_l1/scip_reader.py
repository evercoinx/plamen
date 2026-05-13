"""SCIP protobuf index reader for L1 mode primitives.

Reads the SCIP (Sourcegraph Code Intelligence Protocol) index files
produced by:
  * `scip-go index <path>`              (Go sources)
  * `rust-analyzer scip <path>`         (Rust workspaces)
  * any other SCIP-compliant indexer (scip-java, scip-typescript, ...)

SCIP is a language-agnostic protobuf schema for code intelligence.
One static artifact per repo; queries are sub-millisecond because
they hit an in-memory protobuf, not a live language server. This is
the primitive shim referenced in docs/l1-mode/design.md Section 5.3.

## Install prerequisites

    pip install protobuf>=5.0

## Generate the SCIP protobuf bindings

The protobuf schema lives at https://github.com/sourcegraph/scip.
Generate Python bindings once:

    curl -L -o scip.proto https://raw.githubusercontent.com/sourcegraph/scip/main/scip.proto
    protoc --python_out=plamen_l1/ scip.proto
    # produces plamen_l1/scip_pb2.py

## Produce a SCIP index for a target

    # Go
    cd /path/to/go-ethereum
    scip-go --module-root=. --module-version=local

    # Rust
    cd /path/to/reth
    rust-analyzer scip . --exclude-vendored-libraries

    # Output: index.scip in the current directory

## Query the index

    from plamen_l1.scip_reader import ScipReader
    r = ScipReader("index.scip")
    print(r.find_definition("handleRLPxFrame"))
    print(r.find_references("github.com/ethereum/go-ethereum/p2p/rlpx.handleFrame"))
    print(list(r.list_symbols_in_file("p2p/rlpx/rlpx.go")))

The API is designed to be MCP-wrappable: each method returns JSON-
serializable dicts suitable for handing to an LLM depth agent.

## Cross-platform notes

Works on Windows, macOS, Linux without changes. No WSL2 required.
The scip-go and rust-analyzer scip binaries themselves must be
installed on the host OS; see docs/l1-mode/design.md Section 13.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

try:
    from . import scip_pb2  # type: ignore[import-not-found]
except ImportError:
    scip_pb2 = None  # populated lazily on first use


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SymbolOccurrence:
    """A single symbol occurrence in a source file."""

    symbol: str
    relative_path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    is_definition: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "path": self.relative_path,
            "range": {
                "start": {"line": self.start_line, "col": self.start_col},
                "end": {"line": self.end_line, "col": self.end_col},
            },
            "is_definition": self.is_definition,
        }


@dataclass
class SymbolInformation:
    """Symbol metadata from the SCIP index (kind, docs, signature)."""

    symbol: str
    display_name: str = ""
    kind: str = ""
    signature: str = ""
    documentation: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "display_name": self.display_name,
            "kind": self.kind,
            "signature": self.signature,
            "documentation": self.documentation,
        }


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class ScipReader:
    """Read-only query API over a SCIP index file."""

    def __init__(self, index_path: str | os.PathLike[str]) -> None:
        global scip_pb2
        if scip_pb2 is None:
            raise RuntimeError(
                "scip_pb2 not generated. Run:\n"
                "  curl -L -o scip.proto https://raw.githubusercontent.com/sourcegraph/scip/main/scip.proto\n"
                "  protoc --python_out=plamen_l1/ scip.proto"
            )

        self.index_path = Path(index_path)
        if not self.index_path.exists():
            raise FileNotFoundError(f"SCIP index not found: {self.index_path}")

        self._index = scip_pb2.Index()  # type: ignore[attr-defined]
        with open(self.index_path, "rb") as f:
            self._index.ParseFromString(f.read())

        # Build lookup tables for fast query.
        self._definitions: Dict[str, SymbolOccurrence] = {}
        self._references: Dict[str, List[SymbolOccurrence]] = {}
        self._file_symbols: Dict[str, List[SymbolOccurrence]] = {}
        self._symbol_info: Dict[str, SymbolInformation] = {}

        self._build_lookup_tables()

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def _build_lookup_tables(self) -> None:
        # SCIP Role constants: Definition = 1 (see scip.proto)
        ROLE_DEFINITION = 1

        for doc in self._index.documents:
            rel_path = doc.relative_path
            occurrences: List[SymbolOccurrence] = []

            for occ in doc.occurrences:
                # SCIP ranges are [start_line, start_col, end_line, end_col]
                # or 3-element [line, start_col, end_col] if on a single line.
                r = list(occ.range)
                if len(r) == 3:
                    start_line, start_col, end_col = r
                    end_line = start_line
                elif len(r) == 4:
                    start_line, start_col, end_line, end_col = r
                else:
                    continue  # malformed occurrence

                is_def = bool(occ.symbol_roles & ROLE_DEFINITION)
                symbol_occ = SymbolOccurrence(
                    symbol=occ.symbol,
                    relative_path=rel_path,
                    start_line=start_line,
                    start_col=start_col,
                    end_line=end_line,
                    end_col=end_col,
                    is_definition=is_def,
                )
                occurrences.append(symbol_occ)

                if is_def:
                    self._definitions[occ.symbol] = symbol_occ
                else:
                    self._references.setdefault(occ.symbol, []).append(symbol_occ)

            self._file_symbols[rel_path] = occurrences

            for sym_info in doc.symbols:
                self._symbol_info[sym_info.symbol] = SymbolInformation(
                    symbol=sym_info.symbol,
                    display_name=sym_info.display_name or "",
                    kind=scip_pb2.SymbolInformation.Kind.Name(sym_info.kind)  # type: ignore[attr-defined]
                    if sym_info.kind else "",
                    signature=(sym_info.signature_documentation.text
                               if sym_info.HasField("signature_documentation") else ""),
                    documentation=list(sym_info.documentation),
                )

    # ------------------------------------------------------------------
    # Public query API (all return JSON-serializable data)
    # ------------------------------------------------------------------

    # Matches the last meaningful descriptor component of a SCIP symbol moniker.
    # SCIP symbols look like:
    #   'scip-go gomod github.com/.../pkg SomeType#Method().'
    #   'scip-go gomod github.com/.../pkg/SomeFn().'
    #   'rust-analyzer cargo reth-network 1.0 crate::net::Handler#impl#handle().'
    # The "name" an auditor types is the trailing identifier before the
    # descriptor suffix (`()` for functions, `#` for types, `.` for fields, etc.).
    _DESCRIPTOR_TAIL_RE = __import__("re").compile(
        r"([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?[.#:/]?\s*$"
    )

    @classmethod
    def _extract_name_from_symbol(cls, symbol: str) -> str:
        """Extract the trailing identifier from a SCIP symbol moniker."""
        if not symbol or symbol.startswith("local "):
            return ""
        # Drop any trailing `.`, `#`, `()`
        tail = symbol.rstrip()
        while tail.endswith(("()", ".", "#", ":", "/")):
            if tail.endswith("()"):
                tail = tail[:-2]
            else:
                tail = tail[:-1]
        # Take the identifier after the last `/`, `.`, `#`, `:`, or space
        for sep in ("/", ".", "#", ":", " "):
            if sep in tail:
                tail = tail.rsplit(sep, 1)[-1]
        return tail

    def find_definition(self, symbol_or_name: str) -> Optional[dict]:
        """Find the definition site of a symbol.

        Accepts either a fully-qualified SCIP symbol or a bare display name.
        Returns the occurrence as a dict, or None if not found.
        """
        # Exact symbol lookup
        if symbol_or_name in self._definitions:
            return self._definitions[symbol_or_name].to_dict()

        # Name-based lookup: scan SymbolInformation.display_name first
        # (scip-rust and some scip-go locals populate this).
        for sym, info in self._symbol_info.items():
            if info.display_name == symbol_or_name and sym in self._definitions:
                return self._definitions[sym].to_dict()

        # Fallback: scip-go does NOT populate display_name for top-level
        # symbols — the identifier is embedded in the symbol moniker.
        # Extract the trailing descriptor and match by equality.
        for sym in self._definitions:
            if self._extract_name_from_symbol(sym) == symbol_or_name:
                return self._definitions[sym].to_dict()

        return None

    def find_references(self, symbol_or_name: str) -> List[dict]:
        """Find all reference sites for a symbol.

        Returns a list of occurrences. Empty list if none.
        """
        results: List[SymbolOccurrence] = []

        # Direct symbol lookup
        if symbol_or_name in self._references:
            results.extend(self._references[symbol_or_name])
            return [occ.to_dict() for occ in results]

        # Name-based: find all symbols matching the display name, aggregate refs
        matching_symbols = [
            sym for sym, info in self._symbol_info.items()
            if info.display_name == symbol_or_name
        ]
        # Fallback for scip-go top-level symbols (empty display_name)
        for sym in self._definitions:
            if sym not in matching_symbols and self._extract_name_from_symbol(sym) == symbol_or_name:
                matching_symbols.append(sym)
        for sym in self._references:
            if sym not in matching_symbols and self._extract_name_from_symbol(sym) == symbol_or_name:
                matching_symbols.append(sym)

        for sym in matching_symbols:
            results.extend(self._references.get(sym, []))

        return [occ.to_dict() for occ in results]

    def list_symbols_in_file(self, relative_path: str) -> List[dict]:
        """List all symbol occurrences in a given file.

        Useful for attack-surface enumeration: "list all handlers in
        p2p/rlpx/rlpx.go" maps to list_symbols_in_file(...) filtered by
        symbol kind.
        """
        occurrences = self._file_symbols.get(relative_path, [])
        return [occ.to_dict() for occ in occurrences]

    def workspace_symbol(self, query: str, limit: int = 50) -> List[dict]:
        """Search symbols by substring of display name or symbol moniker.

        L1 use case: locate all symbols matching 'Handler' or 'Service'
        to enumerate the network attack surface.

        Searches both the SymbolInformation.display_name AND the SCIP
        symbol moniker tail — scip-go leaves display_name empty for top-
        level Go symbols and encodes the name in the symbol string.
        """
        results: List[dict] = []
        query_lower = query.lower()
        seen_symbols: set = set()

        # Pass 1: SymbolInformation.display_name matches (rust-analyzer, locals)
        for sym, info in self._symbol_info.items():
            if query_lower in info.display_name.lower():
                entry = info.to_dict()
                if sym in self._definitions:
                    entry["definition"] = self._definitions[sym].to_dict()
                results.append(entry)
                seen_symbols.add(sym)
                if len(results) >= limit:
                    return results

        # Pass 2: symbol-moniker tail matches (scip-go top-level symbols)
        for sym in self._definitions:
            if sym in seen_symbols or sym.startswith("local "):
                continue
            tail = self._extract_name_from_symbol(sym)
            if tail and query_lower in tail.lower():
                entry = {
                    "symbol": sym,
                    "display_name": tail,
                    "kind": self._symbol_info.get(sym, SymbolInformation(symbol=sym)).kind,
                    "definition": self._definitions[sym].to_dict(),
                }
                results.append(entry)
                seen_symbols.add(sym)
                if len(results) >= limit:
                    return results

        return results

    def stats(self) -> dict:
        """Return index statistics — useful for verifying the index loaded."""
        return {
            "index_path": str(self.index_path),
            "documents": len(self._file_symbols),
            "definitions": len(self._definitions),
            "reference_symbols": len(self._references),
            "symbol_info_entries": len(self._symbol_info),
        }

    def filter_by_prefix(self, prefix: str, output_path: str | os.PathLike[str]) -> dict:
        """Post-hoc subsystem scoping: write a new SCIP artifact containing
        only documents whose relative_path starts with `prefix`.

        This is the Rust-side workaround for T2 multi-scoped runs because
        `rust-analyzer scip` does not support native scoped indexing
        (see rust-lang/rust-analyzer#10669). Workflow:

            1. rust-analyzer scip <workspace>     # whole-workspace index
            2. filter_by_prefix("crates/net/eth-wire/", "scip_eth_wire.index")
            3. query the filtered index for the eth-wire subsystem audit

        Go targets should use `scip-go ./subpath/...` instead — scip-go
        supports native scoped indexing via positional PackagePatterns.
        """
        global scip_pb2
        if scip_pb2 is None:
            raise RuntimeError("scip_pb2 not generated; see ScipReader docstring")

        filtered = scip_pb2.Index()  # type: ignore[attr-defined]
        filtered.metadata.CopyFrom(self._index.metadata)

        retained_paths: list[str] = []
        dropped = 0
        for doc in self._index.documents:
            if doc.relative_path.startswith(prefix):
                new_doc = filtered.documents.add()
                new_doc.CopyFrom(doc)
                retained_paths.append(doc.relative_path)
            else:
                dropped += 1

        # SCIP external_symbols are resolved relative to the workspace,
        # so carry them forward so cross-boundary references remain
        # interpretable in the filtered artifact.
        for ext in self._index.external_symbols:
            filtered.external_symbols.add().CopyFrom(ext)

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as f:
            f.write(filtered.SerializeToString())

        return {
            "source_index": str(self.index_path),
            "output_path": str(out),
            "prefix": prefix,
            "retained_documents": len(retained_paths),
            "dropped_documents": dropped,
            "output_size_bytes": out.stat().st_size,
            "sample_retained_paths": retained_paths[:10],
        }


# ---------------------------------------------------------------------------
# CLI entry point (sanity check from shell)
# ---------------------------------------------------------------------------


def _main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(
            "Usage:\n"
            "  python -m plamen_l1.scip_reader <index.scip> stats\n"
            "  python -m plamen_l1.scip_reader <index.scip> definition <name>\n"
            "  python -m plamen_l1.scip_reader <index.scip> references <name>\n"
            "  python -m plamen_l1.scip_reader <index.scip> file <relative_path>\n"
            "  python -m plamen_l1.scip_reader <index.scip> search <query>",
            file=sys.stderr,
        )
        return 2

    index_path = argv[1]
    cmd = argv[2] if len(argv) >= 3 else "stats"
    reader = ScipReader(index_path)

    if cmd == "stats":
        print(json.dumps(reader.stats(), indent=2))
    elif cmd == "definition" and len(argv) >= 4:
        print(json.dumps(reader.find_definition(argv[3]), indent=2))
    elif cmd == "references" and len(argv) >= 4:
        print(json.dumps(reader.find_references(argv[3]), indent=2))
    elif cmd == "file" and len(argv) >= 4:
        print(json.dumps(reader.list_symbols_in_file(argv[3]), indent=2))
    elif cmd == "search" and len(argv) >= 4:
        print(json.dumps(reader.workspace_symbol(argv[3]), indent=2))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
