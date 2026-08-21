#!/usr/bin/env node

/**
 * Static server for testing GitHub Pages-deployed app.
 * Mimics GH Pages behavior: basePath=/signals-app, 404→index.html SPA rewrite.
 */

import serveHandler from 'serve-handler';
import http from 'http';
import path from 'path';
import url from 'url';
import fs from 'fs';

const PORT = process.env.PORT || 3000;
const OUT_DIR = path.resolve('out');

const server = http.createServer(async (req, res) => {
  // Remove /signals-app prefix (basePath)
  let pathname = new url.URL(req.url, `http://localhost`).pathname;

  if (pathname.startsWith('/signals-app/')) {
    pathname = pathname.slice('/signals-app'.length);
  } else if (pathname === '/signals-app') {
    pathname = '/';
  }

  // Rewrite pathname back to url for serve-handler
  req.url = pathname;

  // Try to serve the requested file
  const filePath = path.join(OUT_DIR, pathname);

  // Check if it's a directory or doesn't exist
  if (!fs.existsSync(filePath)) {
    // Rewrite to index.html for SPA (unless it's a known asset)
    if (
      !pathname.includes('.') ||
      pathname.endsWith('.html') ||
      pathname.includes('/_next/')
    ) {
      req.url = '/index.html';
    } else {
      // 404 for missing assets
      res.writeHead(404);
      res.end('Not found');
      return;
    }
  }

  await serveHandler(req, res, {
    public: OUT_DIR,
    cleanUrls: false,
    etag: false,
  });
});

server.listen(PORT, () => {
  console.log(`🚀 Static server running on http://localhost:${PORT}/signals-app`);
  console.log(`   Serving from: ${OUT_DIR}`);
});
