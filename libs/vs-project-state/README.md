# vs-project-state

Typed persistence for the `.vs` directory in an in-place VibeSys project.

The library owns portable project manifests, run manifests, completed-round
records, input fingerprints, and machine-local operational paths. It does not
invoke Git, construct agents, resolve compute backends, or read provider
credentials. Those application concerns remain in VibeSys.
