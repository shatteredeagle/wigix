-- Draws the wigix tray icons (pixel art) in Aseprite batch mode. Run via tools/make_icons.sh.
-- Each icon is a tiny WigiDash: bezel, 2x2 tile screen, stand, and a status dot in the corner.
local OUT = app.params["out"]
local P = {
  bezel = Color{ r = 201, g = 209, b = 220 }, shade = Color{ r = 128, g = 136, b = 150 },
  screen = Color{ r = 14, g = 17, b = 24 },   blue = Color{ r = 86, g = 156, b = 255 },
  green = Color{ r = 74, g = 214, b = 130 },  amber = Color{ r = 245, g = 190, b = 60 },
  red = Color{ r = 240, g = 84, b = 84 },     grey = Color{ r = 128, g = 136, b = 150 },
  clear = Color{ r = 0, g = 0, b = 0, a = 0 }, white = Color{ r = 232, g = 236, b = 242 },
}
local STATE = {
  connected    = { dot = P.green, tiles = { P.blue, P.green, P.green, P.blue } },
  disconnected = { dot = P.red,   tiles = { P.shade, P.shade, P.shade, P.shade } },
  paused       = { dot = P.grey,  tiles = { P.shade, P.shade, P.shade, P.shade }, pause = true },
  attention    = { dot = P.amber, tiles = { P.blue, P.amber, P.green, P.blue } },
}

local function rect(img, x0, y0, x1, y1, c)
  for y = y0, y1 do for x = x0, x1 do img:putPixel(x, y, c) end end
end

-- layouts: screen box, tile grid, stand, status dot (all inclusive pixel coordinates)
local LAYOUT = {
  [16] = { sx0 = 0, sy0 = 2, sx1 = 15, sy1 = 11, bez = 1, gap = 1, stand = { 6, 12, 9, 13 }, base = { 4, 14, 11, 14 },
           dot = { 10, 8, 15, 13 } },
  [24] = { sx0 = 0, sy0 = 3, sx1 = 23, sy1 = 17, bez = 2, gap = 1, stand = { 9, 18, 14, 20 }, base = { 6, 21, 17, 22 },
           dot = { 15, 12, 23, 20 } },
}

-- the approved 16x16 pixel art (hand-tuned; the 24x24 below is generated from LAYOUT)
local function draw16(img, S)
  rect(img, 0, 2, 15, 11, P.bezel); rect(img, 1, 3, 14, 10, P.screen)
  rect(img, 2, 4, 7, 6, S.tiles[1]);  rect(img, 9, 4, 13, 6, S.tiles[2])
  rect(img, 2, 8, 7, 9, S.tiles[3]);  rect(img, 9, 8, 13, 9, S.tiles[4])
  rect(img, 6, 12, 9, 13, P.shade); rect(img, 4, 14, 11, 14, P.bezel)
  rect(img, 11, 10, 15, 14, P.screen); rect(img, 12, 11, 14, 13, S.dot)
  for _, p in ipairs({ { 11, 10 }, { 15, 10 }, { 11, 14 }, { 15, 14 } }) do img:putPixel(p[1], p[2], P.clear) end
end

local function draw(state, n)
  local L, S = LAYOUT[n], STATE[state]
  local spr = Sprite(n, n)
  spr.filename = string.format("%s/wigix-%s-%d.aseprite", OUT, state, n)
  local img = spr.cels[1].image
  img:clear(P.clear)
  if n == 16 then draw16(img, S); spr:saveAs(spr.filename); spr:close(); return end
  rect(img, L.sx0, L.sy0, L.sx1, L.sy1, P.bezel)                                     -- bezel
  local ix0, iy0, ix1, iy1 = L.sx0 + L.bez, L.sy0 + L.bez, L.sx1 - L.bez, L.sy1 - L.bez
  rect(img, ix0, iy0, ix1, iy1, P.screen)                                            -- screen
  local w, h = ix1 - ix0 + 1, iy1 - iy0 + 1
  local tw, th = (w - L.gap - 2) // 2, (h - L.gap - 2) // 2                          -- 2x2 tiles with a 1px margin
  local k = 1
  for r = 0, 1 do for c = 0, 1 do
    local x0 = ix0 + 1 + c * (tw + L.gap)
    local y0 = iy0 + 1 + r * (th + L.gap)
    rect(img, x0, y0, x0 + tw - 1, y0 + th - 1, S.tiles[k]); k = k + 1
  end end
  rect(img, L.stand[1], L.stand[2], L.stand[3], L.stand[4], P.shade)                 -- neck
  rect(img, L.base[1], L.base[2], L.base[3], L.base[4], P.bezel)                     -- foot
  local dx0, dy0, dx1, dy1 = table.unpack(L.dot)                                     -- status dot with a dark ring
  rect(img, dx0, dy0, dx1, dy1, P.screen)
  rect(img, dx0 + 1, dy0 + 1, dx1 - 1, dy1 - 1, S.dot)
  for _, p in ipairs({ { dx0 + 1, dy0 + 1 }, { dx1 - 1, dy0 + 1 }, { dx0 + 1, dy1 - 1 }, { dx1 - 1, dy1 - 1 } }) do
    img:putPixel(p[1], p[2], P.screen)                                               -- round the dot's corners
  end
  spr:saveAs(spr.filename)
  spr:close()
end

for state, _ in pairs(STATE) do for _, n in ipairs({ 16, 24 }) do draw(state, n) end end
