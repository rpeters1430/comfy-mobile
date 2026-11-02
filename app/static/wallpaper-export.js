/* Pixel wallpaper export: client-side only. Never alters original ComfyUI output. */
(function (root) {
  'use strict';
  const DEVICES = [
    { id: 'pixel10-pro-xl', label: 'Pixel 10 Pro XL', width: 1344, height: 2992 },
    { id: 'pixel10-pro', label: 'Pixel 10 Pro', width: 1280, height: 2856 },
    { id: 'pixel10', label: 'Pixel 10', width: 1080, height: 2424 },
    { id: 'pixel-native', label: 'Pixel 7/8/9 (1080×2400)', width: 1080, height: 2400 }
  ];
  function geometry(sourceWidth, sourceHeight, width, height, margin) {
    if (![sourceWidth, sourceHeight, width, height].every(v => Number.isFinite(v) && v > 0)) throw Error('Invalid image dimensions');
    const m = Math.max(0, Math.min(0.2, Number(margin) / 100 || 0));
    const safeWidth = width * (1 - m * 2);
    const safeHeight = height * (1 - m * 2);
    const scale = Math.min(safeWidth / sourceWidth, safeHeight / sourceHeight);
    const drawWidth = sourceWidth * scale, drawHeight = sourceHeight * scale;
    return { x: (width - drawWidth) / 2, y: (height - drawHeight) / 2, width: drawWidth, height: drawHeight };
  }
  function cover(sourceWidth, sourceHeight, width, height) {
    const scale = Math.max(width / sourceWidth, height / sourceHeight);
    const drawWidth = sourceWidth * scale, drawHeight = sourceHeight * scale;
    return { x: (width - drawWidth) / 2, y: (height - drawHeight) / 2, width: drawWidth, height: drawHeight };
  }
  function pixelImage(canvas, image, device, margin, overlay) {
    const ctx = canvas.getContext('2d');
    const w = device.width, h = device.height;
    canvas.width = w; canvas.height = h;
    const originalWidth = image.naturalWidth || image.width;
    const originalHeight = image.naturalHeight || image.height;
    const bg = cover(originalWidth, originalHeight, w, h);
    ctx.save();
    ctx.filter = 'blur(55px) brightness(0.8)';
    // Overscan hides blurred edge transparency.
    ctx.drawImage(image, bg.x - 65, bg.y - 65, bg.width + 130, bg.height + 130);
    ctx.restore();
    const rect = geometry(originalWidth, originalHeight, w, h, margin);
    // Feather a modest border so background and foreground have no hard seam.
    const layer = document.createElement('canvas');
    layer.width = w; layer.height = h;
    const layerCtx = layer.getContext('2d');
    layerCtx.drawImage(image, rect.x, rect.y, rect.width, rect.height);
    const edge = Math.max(12, Math.round(Math.min(rect.width, rect.height) * .035));
    const mask = layerCtx.createLinearGradient(rect.x, 0, rect.x + rect.width, 0);
    mask.addColorStop(0, 'rgba(0,0,0,0)');
    mask.addColorStop(Math.min(.5, edge / rect.width), 'black');
    mask.addColorStop(Math.max(.5, 1 - edge / rect.width), 'black');
    mask.addColorStop(1, 'rgba(0,0,0,0)');
    layerCtx.globalCompositeOperation = 'destination-in';
    layerCtx.fillStyle = mask; layerCtx.fillRect(0, 0, w, h);
    const vertical = layerCtx.createLinearGradient(0, rect.y, 0, rect.y + rect.height);
    vertical.addColorStop(0, 'rgba(0,0,0,0)');
    vertical.addColorStop(Math.min(.5, edge / rect.height), 'black');
    vertical.addColorStop(Math.max(.5, 1 - edge / rect.height), 'black');
    vertical.addColorStop(1, 'rgba(0,0,0,0)');
    layerCtx.fillStyle = vertical; layerCtx.fillRect(0, 0, w, h);
    ctx.drawImage(layer, 0, 0);
    if (overlay) {
      const zoom = Math.min(.18, Math.max(0, Number(overlay) / 100 || 0));
      ctx.strokeStyle = '#ffdf78'; ctx.lineWidth = Math.max(4, w / 260);
      ctx.setLineDash([24, 14]);
      ctx.strokeRect(w * zoom, h * zoom, w * (1 - 2 * zoom), h * (1 - 2 * zoom));
    }
  }
  async function imageFromUrl(url) {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) throw Error('Image unavailable (' + response.status + ')');
    const blob = await response.blob();
    if (!blob.type.startsWith('image/')) throw Error('Only image outputs support wallpaper export');
    const objectUrl = URL.createObjectURL(blob);
    try {
      const image = new Image();
      image.src = objectUrl;
      await image.decode();
      return image;
    } finally {
      // Image decoding retains the pixels after the source URL is revoked.
      URL.revokeObjectURL(objectUrl);
    }
  }
  async function open(item) {
    if (!item?.url) throw Error('No image to export');
    const dialog = document.createElement('dialog');
    dialog.className = 'wallpaper-export-dialog';
    dialog.setAttribute('aria-label', 'Pixel cinematic wallpaper export');
    const title = document.createElement('h2'); title.textContent = 'Pixel wallpaper export';
    const help = document.createElement('p');
    help.textContent = 'Creates a separate copy with extra background around the image. The original stays unchanged. Yellow outline previews an approximate Pixel crop, not the actual Android effect.';
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Pixel device');
    DEVICES.forEach(d => { const opt = document.createElement('option'); opt.value = d.id; opt.textContent = d.label; select.append(opt); });
    const label = document.createElement('label'); label.textContent = 'Safety padding: 12%';
    const slider = document.createElement('input');
    slider.type = 'range'; slider.min = '0'; slider.max = '20'; slider.step = '1'; slider.value = '12';
    slider.setAttribute('aria-label', 'Safety padding percent');
    const zoomLabel = document.createElement('label'); zoomLabel.textContent = 'Preview simulated crop: 12%';
    const zoom = document.createElement('input');
    zoom.type = 'range'; zoom.min = '0'; zoom.max = '20'; zoom.step = '1'; zoom.value = '12';
    zoom.setAttribute('aria-label', 'Preview simulated zoom percent');
    const preview = document.createElement('canvas'); preview.className = 'wallpaper-export-preview'; preview.setAttribute('role', 'img'); preview.setAttribute('aria-label', 'Padded wallpaper with approximate cinematic crop guide');
    const status = document.createElement('p'); status.setAttribute('role', 'status'); status.textContent = 'Loading photo…';
    const actions = document.createElement('div'); actions.className = 'row';
    const exportButton = document.createElement('button'); exportButton.className = 'primary'; exportButton.textContent = 'Save safe wallpaper'; exportButton.disabled = true;
    const close = document.createElement('button'); close.className = 'secondary'; close.textContent = 'Close';
    actions.append(exportButton, close);
    dialog.append(title, help, select, label, slider, zoomLabel, zoom, preview, status, actions);
    document.body.append(dialog);
    dialog.showModal();
    const cleanup = () => { if (dialog.open) dialog.close(); dialog.remove(); };
    close.onclick = cleanup;
    dialog.addEventListener('close', cleanup, { once: true });
    try {
      const image = await imageFromUrl(item.url);
      if (!dialog.isConnected) return;
      const device = () => DEVICES.find(d => d.id === select.value) || DEVICES[0];
      const repaint = () => {
        label.textContent = 'Safety padding: ' + slider.value + '%';
        zoomLabel.textContent = 'Preview simulated crop: ' + zoom.value + '%';
        // Preview uses a small buffer to avoid allocating multiple full-screen frames.
        const d = device(), previewDevice = { width: Math.round(d.width * .22), height: Math.round(d.height * .22) };
        pixelImage(preview, image, previewDevice, Number(slider.value), Number(zoom.value));
      };
      select.onchange = repaint; slider.oninput = repaint; zoom.oninput = repaint;
      repaint();
      status.textContent = 'Choose padding and preview crop tolerance before saving.';
      exportButton.disabled = false;
      exportButton.onclick = async () => {
        exportButton.disabled = true;
        status.textContent = 'Preparing exact-resolution PNG…';
        try {
          const d = device(), canvas = document.createElement('canvas');
          pixelImage(canvas, image, d, Number(slider.value), 0);
          const blob = await new Promise((resolve, reject) => canvas.toBlob(b => b ? resolve(b) : reject(Error('PNG export failed')), 'image/png'));
          const url = URL.createObjectURL(blob);
          const anchor = document.createElement('a');
          anchor.href = url;
          anchor.download = (item.filename || 'wallpaper').replace(/\.[^.]+$/, '') + '-' + d.id + '-cinematic-safe.png';
          document.body.append(anchor); anchor.click(); anchor.remove();
          setTimeout(() => URL.revokeObjectURL(url), 30000);
          status.textContent = 'Saved to Downloads. Apply the new copy using Pixel Wallpaper & style.';
        } catch (error) { status.textContent = 'Export failed: ' + error.message; }
        finally { exportButton.disabled = false; }
      };
    } catch (error) { status.textContent = 'Cannot preview this photo: ' + error.message; }
  }
  root.WallpaperExport = { open, geometry, cover, devices: DEVICES };
  if (typeof module !== 'undefined') module.exports = { geometry, cover, devices: DEVICES };
})(typeof window !== 'undefined' ? window : globalThis);
