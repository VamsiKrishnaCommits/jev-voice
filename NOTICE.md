# Dependency and provenance notes

The Python agent and turn controller were brought forward from the local Jev prototype. This repository adds the dedicated WebRTC server adapter, TypeScript client, and standalone comparison app around that implementation.

Pipecat is installed as a dependency, not copied or forked. Its frames, provider services, Silero integration and SmallWebRTC transport remain internal plumbing. The public browser API is implemented here and does not require Pipecat's UI/client. aiortc supplies the underlying Python WebRTC implementation. Other installed dependencies retain their own licenses and notices; consult their installed metadata before distribution.

No license has been chosen for the project-authored code. Public visibility is not an open-source license. Review licensing and dependency obligations before reuse or redistribution. Names and package scopes are provisional; this repository makes no claim to registry ownership.
