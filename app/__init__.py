"""Bootstrap: додаємо `app/grpc_generated/` у sys.path так, щоб генеровані
Connect/protobuf stubs (`import codex.v1.auth_pb2 ...`) знаходились без
переписування absolute-imports у згенерованому коді."""

import os
import sys

_GRPC_GENERATED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grpc_generated")
if os.path.isdir(_GRPC_GENERATED) and _GRPC_GENERATED not in sys.path:
    sys.path.insert(0, _GRPC_GENERATED)
