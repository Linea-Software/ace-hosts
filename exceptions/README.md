# Category exceptions

Files in this directory contain exact hostnames that are false positives for
one ACE behavioural category but must not become global whitelist entries.

The merge pipeline combines `whitelist.txt` with
`exceptions/<category>.txt` for the category being built. Matching is exact:
adding `sub.example.com` does not exempt `example.com`, sibling hosts, or
other subdomains.

Keep these lists narrow. Do not add broad shared suffixes such as
`amazonaws.com`, `cloudfront.net`, or `googleapis.com`; shared
infrastructure should only be excepted when a specific hostname is known to
cause unrelated collateral blocking.
