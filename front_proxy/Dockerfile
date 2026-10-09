FROM python:3.11-slim
WORKDIR /app
RUN pip install flask requests gunicorn
COPY front_proxy.py /app/front_proxy.py
EXPOSE 8082
CMD ["gunicorn", "-w", "2", "-k", "gthread", "--threads", "4", "-b", "0.0.0.0:8082", "front_proxy:app"]
