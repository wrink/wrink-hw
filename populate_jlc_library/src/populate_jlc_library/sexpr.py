"""Minimal KiCad S-expression reader.

Quoted strings become plain ``str``; bare atoms become :class:`Sym` (a ``str`` subclass), so
``(property "Reference" "R1")`` parses to ``[Sym('property'), 'Reference', 'R1']``.
"""
from __future__ import annotations

import re
from typing import Iterator, List, Union

_TOKEN = re.compile(r'\(|\)|"((?:[^"\\]|\\.)*)"|[^\s()"]+')
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


class Sym(str):
    """A bare (unquoted) atom such as ``symbol`` or ``yes``."""


Node = List[Union["Node", str]]


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), s)


def parse(text: str) -> Node:
    """Parse one top-level expression and return it as a nested list."""
    stack: List[Node] = [[]]
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if tok == "(":
            stack.append([])
        elif tok == ")":
            if len(stack) < 2:
                raise ValueError("unbalanced ')' in S-expression")
            node = stack.pop()
            stack[-1].append(node)
        elif tok.startswith('"'):
            stack[-1].append(_unescape(m.group(1)))
        else:
            stack[-1].append(Sym(tok))
    if len(stack) != 1:
        raise ValueError("unbalanced '(' in S-expression")
    if not stack[0]:
        raise ValueError("empty S-expression")
    return stack[0][0]


def head(node) -> str:
    """Name of a node: ``(symbol ...)`` -> ``'symbol'``; '' for anything else."""
    if isinstance(node, list) and node and isinstance(node[0], Sym):
        return str(node[0])
    return ""


def children(node: Node, name: str) -> Iterator[Node]:
    for c in node:
        if head(c) == name:
            yield c


def first(node: Node, name: str):
    return next(children(node, name), None)


def properties(node: Node) -> dict:
    """``(property "Name" "Value" ...)`` children as ``{Name: Value}``."""
    out = {}
    for p in children(node, "property"):
        if len(p) >= 3 and isinstance(p[1], str) and isinstance(p[2], str):
            out[str(p[1])] = str(p[2])
    return out
