import time
from botocore.exceptions import ClientError
from .storage import client
from .config import settings

def initialize():
    for _ in range(90):
        try:
            s3=client()
            try: s3.head_bucket(Bucket=settings.s3_bucket)
            except ClientError as exc:
                if exc.response['Error']['Code'] not in ('404','NoSuchBucket'): raise
                s3.create_bucket(Bucket=settings.s3_bucket)
            # Never make this bucket public.
            try: s3.delete_bucket_policy(Bucket=settings.s3_bucket)
            except ClientError as exc:
                if exc.response['Error']['Code'] not in ('NoSuchBucketPolicy','NoSuchPolicy'): raise
            return
        except Exception: time.sleep(2)
    raise RuntimeError('Private receipt storage did not become ready.')
if __name__=='__main__': initialize()
