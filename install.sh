#!/usr/bin/env bash
set -e

INSTALL_DIR="${1:-$HOME/.local/bin}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"

echo "Installing srctl to $INSTALL_DIR ..."

mkdir -p "$INSTALL_DIR"

cat > "$INSTALL_DIR/srctl" << EOF
#!/usr/bin/env bash
exec python3 "${SCRIPT_DIR}/srctl.py" "\$@"
EOF

chmod +x "$INSTALL_DIR/srctl"

echo "Done! Run 'srctl --help' to get started."
echo ""
if ! echo "$PATH" | tr ':' '\n' | grep -qxF "$INSTALL_DIR"; then
    echo "Add this to your ~/.zshrc:"
    echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
fi
