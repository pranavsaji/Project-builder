# tools/project_parser.py
from __future__ import annotations
import re
from typing import Dict, List, Tuple

# Import the refined patterns and helpers
from tools.parsing_utils import (
    FENCE_RE,
    TREE_LINE_RE,
    HEADING_PATH_EXTRACT_RE,
    HEADING_FILENAME_EXTRACT_RE,
    _norm_rel,
    _strip_root_prefix,
    _is_texty_name
)

class ProjectParser:
    """
    A robust, state-aware parser that walks through the dump line-by-line
    to correctly parse the ASCII tree and associate code blocks with their
    most recent contextual heading.
    """

    def __init__(self, raw_dump: str, root_name: str, logger=print):
        self.raw_dump = raw_dump
        self.root_name = root_name
        self.logger = logger

    def parse(self) -> Tuple[Dict[str, str], List[str]]:
        """
        Executes the full, robust parsing workflow.
        """
        all_declared_files = self._discover_paths_from_tree()
        self.logger(f"[parser] Found {len(all_declared_files)} file paths in the ASCII tree.")
        if not all_declared_files:
            self.logger("[parser:warn] ASCII tree parsing returned no files. Check dump format.")
        
        found_files_with_content = self._map_content_to_files(all_declared_files)
        self.logger(f"[parser] Mapped content for {len(found_files_with_content)} files.")

        found_paths = set(found_files_with_content.keys())
        missing_files = sorted([
            path for path in all_declared_files if path not in found_paths
        ])

        return found_files_with_content, missing_files

    def _discover_paths_from_tree(self) -> List[str]:
        """Parses the ASCII tree to get a complete list of all expected files."""
        tree_files = []
        in_tree_section = False
        path_stack = []
        lines = self.raw_dump.splitlines()

        for line in lines:
            stripped_line = line.strip()
            if not in_tree_section:
                # The tree starts with a line like "calendar-hub/"
                if stripped_line == self.root_name + "/":
                    in_tree_section = True
                continue

            if not stripped_line: continue

            # If we hit a major heading without tree characters, the tree is over
            if stripped_line.startswith("#") and "─" not in line:
                break
                
            match = TREE_LINE_RE.match(line)
            if not match: continue

            data = match.groupdict()
            # A robust way to calculate depth is by the raw character index of the name
            indent_level = match.start('name')
            name = data['name'].split('#')[0].strip()

            while path_stack and path_stack[-1]['level'] >= indent_level:
                path_stack.pop()
            
            current_path_parts = [p['name'] for p in path_stack]
            
            if name.endswith("/"):
                dir_name = name.rstrip("/")
                path_stack.append({'level': indent_level, 'name': dir_name})
            else:
                full_path = "/".join(current_path_parts + [name])
                if _is_texty_name(full_path):
                    tree_files.append(full_path)
        return tree_files

    def _map_content_to_files(self, all_files: List[str]) -> Dict[str, str]:
        """
        Walks the document statefully to associate code blocks with the most
        recently mentioned file path in a heading.
        """
        found_content = {}
        lines = self.raw_dump.splitlines()
        
        current_file_context = None
        in_fence = False
        current_block_lines = []

        for line in lines:
            if in_fence:
                if FENCE_RE.match(line):
                    in_fence = False
                    if current_file_context:
                        # A special case for the user's dump where two mappers are in one file
                        if "continued" in (current_file_context.get("heading", "")).lower():
                            if current_file_context["path"] in found_content:
                                found_content[current_file_context["path"]] += "\n\n" + "\n".join(current_block_lines)
                                self.logger(f"[parser] Appended content to {current_file_context['path']}")
                            else:
                                found_content[current_file_context["path"]] = "\n".join(current_block_lines)
                                self.logger(f"[parser] Mapped content to {current_file_context['path']}")
                        else:
                             found_content[current_file_context["path"]] = "\n".join(current_block_lines)
                             self.logger(f"[parser] Mapped content to {current_file_context['path']}")
                        
                        current_file_context = None # Reset context after use
                    current_block_lines = []
                else:
                    current_block_lines.append(line)
            else:
                if FENCE_RE.match(line):
                    in_fence = True
                    continue

                # Check if this line is a heading that establishes a file context
                path_match = HEADING_PATH_EXTRACT_RE.search(line)
                if path_match:
                    path = _strip_root_prefix(_norm_rel(path_match.group("path")), self.root_name)
                    if path in all_files:
                        current_file_context = {"path": path, "heading": line}
                        self.logger(f"[parser] Set file context to: {path}")
                        continue
                
                filename_match = HEADING_FILENAME_EXTRACT_RE.search(line)
                if filename_match:
                    fname = filename_match.group("filename")
                    possible_paths = [p for p in all_files if p.endswith(f"/{fname}") or p == fname]
                    if possible_paths:
                        current_file_context = {"path": possible_paths[0], "heading": line}
                        self.logger(f"[parser] Set file context to: {possible_paths[0]}")
        
        return found_content