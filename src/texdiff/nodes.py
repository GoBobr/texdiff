"""Nodes - the intermediate representation produced by parsing.

texdiff never operates on pylatexenc node classes directly: we convert
them to our own lightweight :class:`Node` records first. Each node

* carries ``text`` - its EXACT source span, so unchanged nodes round
  trip byte-identically (critical for listings, attachments and
  generated tables);
* carries ``children`` - a list of sub-nodes (only for group-like
  nodes: environments, braced groups, macro arguments);
* can be *atomic* (math, verbatim, unknown macros): no children, never
  diffed internally, compared on exact source equality;
* knows whether it may carry change markup inside it (``atom`` False
  only for text-ish nodes; markup is NEVER injected into atomic
  nodes - that injector is what breaks markup in latexdiff).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass(frozen=True)
class Node:
    """One node of the concrete syntax tree.

    Attributes:
        kind: node type discriminator, e.g. ``"env"`` (environment),
            ``"group"`` (braced group / macro argument), ``"macro"``,
            ``"text"`` (plain characters run), ``"math"`` (inline or
            display math), ``"comment"``, ``"specials"``, ``"verb"``
            (verbatim-like environment, atomic).
        text: exact source text of the node INCLUDING delimiters
            (``\\begin{...}...\\end{...}`` for environments, the braces
            for groups). Unchanged nodes are emitted using this text
            verbatim, so formatting survives the diff round trip.
        name: environment or macro name (``None`` for text).
        children: sub-nodes for group-like nodes; empty otherwise.
        atom: if True the node is atomic - never diffed internally and
            never receives inline markup (math, verbatim, unknown
            macros, specials).
    """

    kind: str
    text: str
    name: Optional[str] = None
    children: list[Node] = field(default_factory=list)
    atom: bool = True

    def walk(self) -> Iterator["Node"]:
        """Yield this node and all descendants, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()

    def signature(self) -> str:
        """Return a canonical key used to align two nodes.

        Two nodes are aligned candidate-wise when their signatures are
        equal and their exact source differs (then we diff inside);
        nodes with equal signature AND equal text are identical.
        """
        if self.name:
            return f"{self.kind}:{self.name}"
        return self.kind


def text_node(s: str) -> Node:
    """Build a plain text run node."""
    return Node(kind="text", text=s, atom=True)


def env(
    name: str,
    *children: Node,
    text: Optional[str] = None,
    atom: bool = False,
) -> Node:
    """Build an environment node (test helper / prototyping)."""
    if text is None:
        body = "".join(c.text for c in children)
        text = f"\\begin{{{name}}}{body}\\end{{{name}}}"
    return Node(kind="env", text=text, name=name, children=list(children), atom=atom)


def group(*children: Node, name: Optional[str] = None, text: Optional[str] = None) -> Node:
    """Build a braced group node (test helper / prototyping)."""
    if text is None:
        body = "".join(c.text for c in children)
        text = "{" + body + "}"
    return Node(kind="group", text=text, name=name, children=list(children), atom=False)
