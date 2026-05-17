# ─────────────────────────────────────────────────────────────────────────────
# JURA ENA 8 — Home Assistant Integration
# Deploy to Home Assistant via SSH
# ─────────────────────────────────────────────────────────────────────────────

-include .env
export

HA_HOST     ?= homeassistant.local
HA_SSH_USER ?= ha
HA_SSH_PORT ?= 22
HA_SSH_KEY  ?= ~/.ssh/homeassistant
INTEGRATION  = jura_ena8

SSH = ssh -i $(HA_SSH_KEY) -p $(HA_SSH_PORT) $(HA_SSH_USER)@$(HA_HOST)

.PHONY: help deploy restart

help:
	@echo ""
	@echo "JURA ENA 8 — Integration Makefile"
	@echo ""
	@echo "  make deploy    — Push integration to Home Assistant"
	@echo "  make restart   — Restart Home Assistant (reload config)"
	@echo ""

## Push the integration files to Home Assistant via SSH tar
deploy:
	@echo "🚀 Deploying $(INTEGRATION) to Home Assistant..."
	@tar -czf - \
		--exclude='**/__pycache__' \
		--exclude='**/*.pyc' \
		--exclude='**/._*' \
		--exclude='.git' \
		--exclude='.env' \
		--exclude='Makefile' \
		--exclude='README.md' \
		--exclude='hacs.json' \
		-C . custom_components \
	| $(SSH) "sudo tar -xzf - -C /config && sudo find /config/custom_components/$(INTEGRATION) -name '._*' -delete"
	@echo "✅ Deployed! Run 'make restart' to apply."

## Restart / reload Home Assistant
restart:
	@echo "🔄 Restarting Home Assistant..."
	@$(SSH) "curl -s -X POST \
		-H 'Authorization: Bearer $$(cat /config/.storage/auth 2>/dev/null | python3 -c \"import json,sys; d=json.load(sys.stdin); print([t.get('"'"'token'"'"',''"'"') for t in d.get('"'"'data'"'"',{}).get('"'"'refresh_tokens'"'"',[]) if t.get('"'"'client_name'"'"')==''"'"'Claude'"'"'][0])\" 2>/dev/null)' \
		http://localhost:8123/api/services/homeassistant/restart" || true
	@echo "✅ Restart initiated."
