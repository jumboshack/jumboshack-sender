FROM python:3.14-slim
WORKDIR /app
# No third-party deps — stdlib urllib only.
COPY sender.py .
CMD ["python", "-u", "sender.py"]
