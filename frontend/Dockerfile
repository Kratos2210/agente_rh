# Frontend Next.js (build de producción). El build context es la RAÍZ del repo
# (ver docker-compose.yml), por eso las rutas COPY empiezan con frontend/.
FROM node:20-slim

WORKDIR /app

# Dependencias (con lock → npm ci, reproducible).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Código del frontend.
COPY frontend/ ./

# El rewrite /api/* → BACKEND_URL se hornea en build (Next evalúa rewrites() al
# compilar). El backend se llama "backend" en la red de Docker Compose.
ENV BACKEND_URL=http://backend:8000
RUN npm run build

EXPOSE 3000
CMD ["npm", "run", "start"]
