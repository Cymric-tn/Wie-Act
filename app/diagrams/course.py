"""The real course graph (data/courses/<id>.yaml, the engine's file) in the shape the concept-map builder reads.

The graph says `titles`, the builder reads `title`. Concepts with `status: draft` are left out, as the app's progress map and
the planner leave them out, so a concept map and the progress map always show the same concepts."""
import re
from pathlib import Path

import yaml

COURSE_ID = re.compile(r"^[\w.-]{1,80}$")


def load_repo_course(courses_dir, course_id):
    """-> the course as a dict, or None when there is no such course (a graph or a table does not need one)."""
    if not COURSE_ID.match(str(course_id or "")) or ".." in str(course_id):
        return None
    path = Path(courses_dir) / f"{course_id}.yaml"
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    units = [{"id": u.get("id"), "concepts": [{"id": c["id"], "title": c.get("titles") or c["id"],
                                               "requires": c.get("requires") or [], "related": c.get("related") or []}
                                              for c in u.get("concepts") or [] if c.get("status") != "draft"]}
             for u in raw.get("units") or []]
    return {"course": raw.get("course"), "units": units}
