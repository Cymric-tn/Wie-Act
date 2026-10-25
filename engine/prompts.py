"""
Versioned prompt files: prompts/<task>.md

    ---
    task: hint
    version: 1
    ---
    ## system
    ... $placeholders (string.Template) ...
    ## user
    ...

Any "## name" heading starts a section. The version is required and is part of every rendered
system prompt's identity (it goes into the cache key), so editing a prompt invalidates its cache.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from string import Template

_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SECTION = re.compile(r"^## +(\S+) *$", re.M)


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class PromptTemplate:
    task: str
    version: str
    sections: dict[str, str]

    @property
    def id(self) -> str:
        return f"{self.task}@v{self.version}"

    def has(self, name: str) -> bool:
        return name in self.sections

    def render(self, section: str, **values) -> str:
        if section not in self.sections:
            raise PromptError(f"{self.id}: no section {section!r}")
        try:
            return Template(self.sections[section]).substitute(**values)
        except KeyError as e:
            raise PromptError(f"{self.id}: section {section!r} needs {e}") from None

    def fields(self, section: str) -> dict[str, str]:
        """'key: value' lines of a section (used for few-shot examples)."""
        out = {}
        for line in self.sections.get(section, "").splitlines():
            k, sep, v = line.partition(":")
            if sep and k.strip():
                out[k.strip()] = v.strip()
        return out


def parse_prompt(text: str, source: str = "<prompt>") -> PromptTemplate:
    m = _FRONT.match(text)
    if not m:
        raise PromptError(f"{source}: missing '---' version header")
    meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    if not meta.get("version") or not meta.get("task"):
        raise PromptError(f"{source}: header needs 'task' and 'version'")
    body = text[m.end():]
    heads = list(_SECTION.finditer(body))
    sections = {h.group(1): body[h.end():(heads[i + 1].start() if i + 1 < len(heads) else len(body))].strip()
                for i, h in enumerate(heads)}
    if "system" not in sections:
        raise PromptError(f"{source}: needs a '## system' section")
    return PromptTemplate(meta["task"], meta["version"], sections)


def load_prompt(prompts_dir: str, name: str) -> PromptTemplate:
    path = os.path.join(prompts_dir, f"{name}.md")
    with open(path, encoding="utf-8") as f:
        return parse_prompt(f.read(), path)
