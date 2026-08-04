"""Storage backends for the wiki API.

`base.py` defines the `WikiStorage` contract; `local.py` is the only
implementation for the POC (local filesystem, GPC-186). It is written so
that a future S3/JuiceFS-backed implementation only needs to satisfy the
same interface — routers and dependency wiring stay unchanged.
"""
