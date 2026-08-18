"""Create a local user for ``AUTH_MODE=users`` without printing the password."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.auth.users import UserStore  # noqa: E402
from enterprise_rag.config import AUTH_DB_PATH  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--department", default="general")
    parser.add_argument("--role", action="append", dest="roles", default=None)
    parser.add_argument("--db", type=Path, default=AUTH_DB_PATH)
    args = parser.parse_args()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("password confirmation mismatch")
    user = UserStore(args.db).create_user(
        args.username,
        password,
        display_name=args.display_name,
        department=args.department,
        roles=args.roles or ["viewer"],
    )
    print(f"created user={user['username']} id={user['id']} roles={','.join(user['roles'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
