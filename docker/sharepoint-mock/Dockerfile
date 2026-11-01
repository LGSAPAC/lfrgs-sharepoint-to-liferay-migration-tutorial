# Mock SharePoint (Online or Server) for developing and testing sp2lr. See mock/sharepoint_mock/app.py.
FROM python:3.12-slim
RUN pip install --no-cache-dir "starlette==1.10.0" "uvicorn==0.54.0" "pyspnego==0.12.4"
WORKDIR /app
COPY mock/sharepoint_mock ./sharepoint_mock
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "sharepoint_mock"]
