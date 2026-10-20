FROM python:3.14-slim
RUN pip install --no-cache-dir numpy eccodes eccodeslib
COPY build_grid.py /app/
ENTRYPOINT ["python", "/app/build_grid.py"]
