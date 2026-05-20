FROM python:3.11-slim

WORKDIR /app

# Copiar archivos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Exponer puerto 8000
EXPOSE 8000

# Comando para correr la app
CMD ["python", "-u", "app.py"]
