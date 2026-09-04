import sys
# from gunicorn.app.base import BaseApplication
# from uvicorn_worker import UvicornWorker 
from main import app  
import uvicorn

# class StandaloneApplication(BaseApplication):
#     def __init__(self, app, options=None):
#         self.options = options or {}
#         self.application = app
#         super().__init__()

#     def load_config(self):
#         config = {key: value for key, value in self.options.items()
#                   if key in self.cfg.settings and value is not None}
#         for key, value in config.items():
#             self.cfg.set(key.lower(), value)

#     def load(self):
#         return self.application

if __name__ == '__main__':
    # options = {
    #     'bind': '0.0.0.0:8000',
    #     'workers': 4,
    #     'worker_class': UvicornWorker, 
    #     'timeout': 3600,
    #     'graceful_timeout': 3600,
    #     'keepalive': 5,
    #     'loglevel': 'debug',
    # }
    # StandaloneApplication(app, options).run()
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )