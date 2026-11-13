"""Opt-in local sample data. Does not run at startup and creates no shared credentials."""
import argparse
from pathlib import Path
from sqlalchemy import select
from .db import Session
from .models import User,Receipt
from .security import hash_password
from .services import create_workspace,upload,confirm
from .jobs import process_receipt

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--email',required=True);parser.add_argument('--password',required=True)
    args=parser.parse_args()
    if not args.email.endswith('.test'): parser.error('Use a synthetic .test address for the development seed.')
    with Session() as db:
        if db.scalar(select(User).where(User.email==args.email)): parser.error('This seed account already exists.')
        u=User(email=args.email,password=hash_password(args.password),verified=True);db.add(u);db.flush()
        w=create_workspace(db,u,'Greenfield demo');db.commit()
        r,_=upload(db,w.id,Path('samples/synthetic-receipt.png').read_bytes());db.commit()
        wid,rid=w.id,r.id
    process_receipt(wid,rid)
    print(f'Created synthetic account and receipt #{rid}. Sign in and review the extraction.')
if __name__=='__main__': main()
