cat > ~/Projects/Tennison/AGENTS.md << 'EOF'
# Tennison — Project Context

## Stack
- Python 3.12, aiogram, loguru, uv

## Architecture
- app/domain/ — business logic
- app/services/ — orchestration
- app/core/ — config, logging

## Rules
- All docstrings in Russian
- No print(), use loguru
- Strict typing everywhere
EOF