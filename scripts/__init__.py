"""ace-hosts — download, merge and split host lists for DNS-based blocking.

This package is the Python successor of the maintenance tooling of the
archived `columndeeply/hosts` repository. The original repo used two shell
scripts — `cleanup.sh` and `merger.sh` — to normalize, deduplicate, sort and
split a unified blocklist into GitHub-friendly 90 MB chunks (hosts00, ...).
See https://github.com/columndeeply/hosts (archived).
"""

__version__ = "0.1.0"
