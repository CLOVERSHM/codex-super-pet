# Private data and optional project adapters

The public repository and ZIP contain only platform code and synthetic fixtures.
Actual session metadata, collected input excerpts, status reports, requirements,
runtime tokens and generated project skills belong in the local data directory
`~/.local/share/delivery-console/private/`. Do not copy this directory into a
checkout or distribution archive. Back it up separately while the server is stopped.

An optional source adapter can be placed at
`private/extensions/adapter.py` inside the local data directory. This adapter is
loaded only from that location; no business source or private translation is
included in the public build. Without an adapter, external sync is unavailable,
but project/session/requirement management works normally. Adapter code is
trusted local code and should not be installed from an untrusted source.

The original legacy database may be kept in the data directory until the local
server is stopped and its records are migrated. Do not publish older Git history
or archived releases: deleting a file in a new commit does not erase old blobs.
