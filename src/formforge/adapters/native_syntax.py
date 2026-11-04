"""Source-span parser extracted from the user-owned Product_IDE_SCH workspace.
See docs/migrations/M3-parser.json for original bytes and extraction ranges.
Only parsing is migrated; no KiCad 9 generation/verification code runs here.
"""
import json
import re


class Node:
    def __init__(self, values, start, source):
        self.values, self.start, self.end, self.source = values, start, 0, source

    @property
    def kind(self):
        return self.values[0]

    @property
    def raw(self):
        return self.source[self.start:self.end]

    def children(self, kind):
        return [v for v in self.values if isinstance(v, Node) and v.kind == kind]

    def one(self, kind):
        rows = self.children(kind)
        if len(rows) != 1:
            raise ValueError('Expected one ' + kind + ' in ' + self.kind)
        return rows[0]



def parse(source):
    stack, roots, end = [], [], 0
    for match in re.finditer(r'\s+|;[^\n]*|"(?:\\.|[^"\\])*"|[()]|[^\s()]+', source):
        if match.start() != end:
            raise ValueError('Unparsed S-expression bytes')
        end = match.end()
        token = match[0]
        if token.isspace() or token.startswith(';'):
            continue
        if token == '(':
            node = Node([], match.start(), source)
            (stack[-1].values if stack else roots).append(node)
            stack.append(node)
        elif token == ')':
            if not stack:
                raise ValueError('Unbalanced close parenthesis')
            stack.pop().end = match.end()
        else:
            if not stack:
                raise ValueError('Atom outside S-expression')
            stack[-1].values.append(json.loads(token) if token.startswith('"') else token)
    if stack or len(roots) != 1 or end != len(source):
        raise ValueError('Incomplete S-expression')
    return roots[0]

