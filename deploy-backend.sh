#!/bin/bash
# Deploy ClipCraft Backend to Railway / Render / Fly.io / VPS

set -e

echo "🚀 ClipCraft Backend Deploy Helper"
echo "=================================="
echo ""
echo "Choose deployment target:"
echo "  1) Railway (easy, $5/mo after trial)"
echo "  2) Render (free tier, spins down)"
echo "  3) Fly.io (free allowance, needs credit card)"
echo "  4) Docker VPS (Oracle Cloud, Hetzner, DigitalOcean)"
echo "  5) Google Cloud Run (scales to zero)"
echo ""
read -p "Enter choice [1-5]: " choice

case $choice in
  1)
    echo "📦 Deploying to Railway..."
    echo "1. Install Railway CLI: npm i -g @railway/cli"
    echo "2. Login: railway login"
    echo "3. Init: railway init (select this folder)"
    echo "4. Deploy: railway up"
    echo ""
    echo "Or connect GitHub repo at railway.app for auto-deploy"
    ;;
  2)
    echo "📦 Deploying to Render..."
    echo "1. Push this repo to GitHub"
    echo "2. Create new Web Service at render.com"
    echo "3. Connect repo, use Docker runtime"
    echo "4. Set env vars: CLIPCRAFT_PORT=5004"
    echo "5. Add disk for /app/data (persistent storage)"
    ;;
  3)
    echo "📦 Deploying to Fly.io..."
    echo "1. Install flyctl: curl -L https://fly.io/install.sh | sh"
    echo "2. Launch: fly launch (select Dockerfile)"
    echo "3. Scale: fly scale memory 2048"
    echo "4. Deploy: fly deploy"
    echo "5. Volume: fly volumes create clipcraft_data --size 10"
    ;;
  4)
    echo "📦 Deploying to VPS (Oracle Cloud / Hetzner / DigitalOcean)..."
    echo ""
    echo "Oracle Cloud Free Tier (RECOMMENDED):"
    echo "  1. Sign up at cloud.oracle.com"
    echo "  2. Create ARM64 instance: 4 OCPU, 24GB RAM"
    echo "  3. SSH in, install Docker:"
    echo "     curl -fsSL https://get.docker.com | sh"
    echo "  4. Clone repo, run:"
    echo "     docker compose up -d"
    echo ""
    echo "Hetzner CX22 (€4.19/mo):"
    echo "  1. Create server, install Docker"
    echo "  2. docker compose up -d"
    ;;
  5)
    echo "📦 Deploying to Google Cloud Run..."
    echo "1. gcloud auth login && gcloud config set project YOUR_PROJECT"
    echo "2. Build & push: gcloud builds submit --tag gcr.io/PROJECT/clipcraft-backend"
    echo "3. Deploy: gcloud run deploy clipcraft-backend --image gcr.io/PROJECT/clipcraft-backend --platform managed --region us-central1 --allow-unauthenticated --memory 4Gi --cpu 2 --timeout 3600"
    echo "4. Set env vars in Cloud Run console"
    ;;
  *)
    echo "Invalid choice"
    exit 1
    ;;
esac

echo ""
echo "✅ After deploy, update Netlify frontend:"
echo "   1. Set NEXT_PUBLIC_API_URL=https://your-backend-url in Netlify env vars"
echo "   2. Update netlify.toml redirects to point to your backend URL"
echo "   3. Redeploy frontend on Netlify"