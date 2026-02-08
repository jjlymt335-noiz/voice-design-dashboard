FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.local/bin:${PATH}"
RUN uv pip install --system -r requirements.txt

COPY push_to_lark.py .

CMD ["python", "push_to_lark.py"]
