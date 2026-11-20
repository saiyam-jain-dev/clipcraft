@echo off
echo Starting ClipCraft...
echo.
echo Starting Python backend...
start "ClipCraft Backend" cmd /k "python server.py"
timeout /t 3 /nobreak >nul
echo.
echo Starting Next.js frontend...
cd nextjs-frontend
start "ClipCraft Frontend" cmd /k "npm run dev"
echo.
echo ClipCraft is starting...
echo Backend: http://127.0.0.1:5004
echo Frontend: http://localhost:3000
echo.
