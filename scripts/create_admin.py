#!/usr/bin/env python3
from __future__ import annotations
import argparse
import getpass
from stockanalyzer.auth import AuthService
from stockanalyzer.config import Settings
from stockanalyzer.db.session import configure_engine

def main():
    parser=argparse.ArgumentParser(description="Create the one-shot administrator account")
    parser.add_argument("--username", required=True)
    args=parser.parse_args()
    password=getpass.getpass("Password: "); confirmation=getpass.getpass("Confirm password: ")
    if password != confirmation: raise SystemExit("Passwords do not match")
    settings=Settings.from_env(); configure_engine(settings.database_url)
    user=AuthService(settings.auth_max_failures, settings.auth_lockout_minutes).create_user(args.username, password, is_admin=True)
    print(f"Administrator created: {user.username}")
if __name__ == "__main__": main()
