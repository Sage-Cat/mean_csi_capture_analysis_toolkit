#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$ROOT_DIR/designs/stable_system_design"
OUT_DIR="$ROOT_DIR/out/stability_system_design"
PLANTUML_JAR="$ROOT_DIR/tools/plantuml.jar"

if [[ ! -f "$PLANTUML_JAR" ]]; then
  echo "Missing PlantUML jar at: $PLANTUML_JAR"
  echo "Download one, for example:"
  echo "  curl -L -o $PLANTUML_JAR https://github.com/plantuml/plantuml/releases/latest/download/plantuml.jar"
  exit 1
fi

if [[ ! -d "$SRC_DIR" ]]; then
  echo "Missing source directory: $SRC_DIR"
  exit 1
fi

mkdir -p "$OUT_DIR"

pass=1
while true; do
  echo "Validation pass $pass..."
  if (cd "$SRC_DIR" && java -Djava.awt.headless=true -jar "$PLANTUML_JAR" -checkonly *.puml); then
    echo "All PlantUML schemas are valid."
    break
  fi
  echo "Validation failed. Fix .puml sources and rerun."
  exit 1
done

echo "Building SVG diagrams to $OUT_DIR ..."
(cd "$SRC_DIR" && java -Djava.awt.headless=true -jar "$PLANTUML_JAR" -tsvg -o ../../out/stability_system_design *.puml)
echo "Build complete."
