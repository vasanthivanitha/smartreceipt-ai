import boto3
from .config import settings

def client():
    return boto3.client('s3', endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_key, aws_secret_access_key=settings.s3_secret,
        region_name='us-east-1')
def put(key, data, mime):
    client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=mime)
def read(key):
    return client().get_object(Bucket=settings.s3_bucket, Key=key)['Body'].read()
def remove(key): client().delete_object(Bucket=settings.s3_bucket, Key=key)
