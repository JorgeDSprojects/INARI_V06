# Wraps the official Node-RED image to seed a base set of flows into any
# fresh /data volume, instead of Node-RED's own "getting started" default
# flow. Docker populates a brand-new named volume from the image's own
# mount-point directory the first time it's attached — so overwriting
# /data/flows.json here is enough; no custom entrypoint needed. An existing
# volume (this project's real uns_manager_nodered_data included) already
# has its own flows.json and is never touched by this.
#
# To update the base flows: export flows from the Node-RED UI (hamburger
# menu -> Export -> all flows -> download), overwrite flows.seed.json with
# it, then `docker compose up -d --build nodered`.
FROM nodered/node-red:latest

COPY --chown=node-red:node-red flows.seed.json /data/flows.json
