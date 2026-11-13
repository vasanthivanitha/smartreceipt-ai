from starlette.responses import JSONResponse

class BodyLimitMiddleware:
    """Bound actual bytes, even for chunked/misreported uploads, before multipart parsing."""
    def __init__(self,app,limit): self.app,self.limit=app,limit
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        chunks=[]; size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect': return
            chunk=message.get('body',b''); size+=len(chunk)
            if size>self.limit:
                return await JSONResponse({'detail':'Request exceeds upload limit.'},status_code=413)(scope,receive,send)
            chunks.append(chunk)
            if not message.get('more_body'): break
        body=b''.join(chunks); sent=False
        async def replay():
            nonlocal sent
            if not sent:
                sent=True; return {'type':'http.request','body':body,'more_body':False}
            return await receive()
        await self.app(scope,replay,send)
