.PHONY: test docs podman-build podman-smoke

test:
	python3 tests/test_project.py
	python3 tests/test_hardware.py

docs:
	./scripts/generate-pdf.sh docs/proyecto.md docs/proyecto.pdf

podman-build:
	podman build -t podman-watchguard-monitor:local -f containers/monitor/Containerfile .

podman-smoke:
	podman run --rm -v ./config:/config:ro podman-watchguard-monitor:local --config /config/watchguard.example.json --iterations 3
