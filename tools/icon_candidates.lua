-- Five tray-icon candidates (16x16 pixel art), four states each. Aseprite batch mode: see tools/make_icons.sh.
local OUT = app.params["out"]
local function C(r, g, b) return Color{ r = r, g = g, b = b } end
local PAL = {
  ["."] = Color{ r = 0, g = 0, b = 0, a = 0 }, O = C(18, 21, 28), B = C(201, 209, 220), s = C(128, 136, 150),
  K = C(14, 17, 24), k = C(57, 65, 82), b = C(86, 156, 255), g = C(74, 214, 130), a = C(245, 190, 60),
  r = C(240, 84, 84), w = C(232, 236, 242), o = C(245, 158, 60),
}
local STATES = {   -- S status color, T/U accent colors
  connected    = { S = PAL.g, T = PAL.b, U = PAL.g },
  disconnected = { S = PAL.r, T = PAL.s, U = PAL.s },
  paused       = { S = PAL.s, T = PAL.s, U = PAL.s },
  attention    = { S = PAL.a, T = PAL.b, U = PAL.a },
}
local function rect(img, x0, y0, x1, y1, c) for y = y0, y1 do for x = x0, x1 do img:putPixel(x, y, c) end end end
local function dot(img, S)   -- 5x5 status dot, dark ring, bottom-right
  rect(img, 11, 10, 15, 14, PAL.O); rect(img, 12, 11, 14, 13, S)
  for _, p in ipairs({ { 11, 10 }, { 15, 10 }, { 11, 14 }, { 15, 14 } }) do img:putPixel(p[1], p[2], PAL["."]) end
end
local function ascii(img, rows, st)
  assert(#rows == 16, "need 16 rows")
  for y, row in ipairs(rows) do
    assert(#row == 16, "row " .. y .. " has " .. #row .. " chars: " .. row)
    for x = 1, 16 do
      local ch = row:sub(x, x)
      local c = (ch == "S") and st.S or (ch == "T") and st.T or (ch == "U") and st.U or PAL[ch]
      assert(c, "unknown pixel char " .. ch)
      img:putPixel(x - 1, y - 1, c)
    end
  end
end

local DESIGNS = {}

-- A: the panel
DESIGNS.A = function(img, st)
  rect(img, 0, 2, 15, 11, PAL.B); rect(img, 1, 3, 14, 10, PAL.K)
  rect(img, 2, 4, 7, 6, st.T);  rect(img, 9, 4, 14 - 1, 6, st.U)
  rect(img, 2, 8, 7, 9, st.U);  rect(img, 9, 8, 14 - 1, 9, st.T)
  rect(img, 6, 12, 9, 13, PAL.s); rect(img, 4, 14, 11, 14, PAL.B)
  dot(img, st.S)
end

-- B: W monogram on a dark screen
DESIGNS.B = function(img, st)
  ascii(img, {
    "................",
    "..BBBBBBBBBBBB..",
    ".BKKKKKKKKKKKKB.",
    "BKKKKKKKKKKKKKKB",
    "BKKKbKKKKKbKKKKB",
    "BKKKbKKbKKbKKKKB",
    "BKKKbKKbKKbKKKKB",
    "BKKKKbKbKbKKKKKB",
    "BKKKKKbKbKKKKKKB",
    "BKKKKKKKKKKKKKKB",
    "BKKKKKKKKKKKKKKB",
    ".BKKKKKKKKKKKKB.",
    "..BBBBBBBBBBBB..",
    "................",
    "................",
    "................",
  }, st)
  -- the W takes the state's accent colour
  for _, p in ipairs({ { 4, 4 }, { 10, 4 }, { 4, 5 }, { 7, 5 }, { 10, 5 }, { 4, 6 }, { 7, 6 }, { 10, 6 }, { 5, 7 }, { 7, 7 }, { 9, 7 }, { 6, 8 }, { 8, 8 } }) do
    img:putPixel(p[1], p[2], st.T)
  end
  dot(img, st.S)
end

-- C: a little bird on its perch
DESIGNS.C = function(img, st)
  ascii(img, {
    "................",
    ".....OOOO.......",
    "....ObbbbO......",
    "...ObbbbbbO.....",
    "...ObbKwbbOoo...",
    "...ObbbbbbOo....",
    "..ObbbbbbbbO....",
    ".ObbbbbbbbbbO...",
    ".ObbwwwwwbbbO...",
    ".ObbwwwwwwbbO...",
    "..ObbwwwwwbO....",
    "..OObbbbbbOO....",
    "...OOOOOOOO.....",
    "....o..o........",
    "....o..o........",
    "................",
  }, st)
  dot(img, st.S)
end

-- D: four tiles, the last one is the status tile
DESIGNS.D = function(img, st)
  local function tile(x0, y0, c)
    rect(img, x0, y0 + 1, x0 + 5, y0 + 4, c); rect(img, x0 + 1, y0, x0 + 4, y0 + 5, c)   -- 6x6, rounded corners
  end
  tile(1, 1, st.T); tile(9, 1, st.T); tile(1, 9, st.T); tile(9, 9, st.S)
end

-- E: a penguin whose belly is a tiny screen showing the status
DESIGNS.E = function(img, st)
  ascii(img, {
    "................",
    ".....BBBBBB.....",
    "....BkkkkkkB....",
    "...BkkkkkkkkB...",
    "...BkwkkkkwkB...",
    "...BkkoooookB...",
    "..BkkkkooKkkkB..",
    "..BkkSSSSSSkkB..",
    ".BkkSSSSSSSSkkB.",
    ".BkkSSSSSSSSkkB.",
    ".BkkSSSSSSSSkkB.",
    "..BkkSSSSSSkkB..",
    "..BBkkSSSSkkBB..",
    "...ooBBBBBBoo...",
    "..ooo......ooo..",
    "................",
  }, st)
end

for letter, fn in pairs(DESIGNS) do
  for state, st in pairs(STATES) do
    local spr = Sprite(16, 16)
    spr.filename = string.format("%s/%s-%s-16.aseprite", OUT, letter, state)
    spr.cels[1].image:clear(PAL["."])
    fn(spr.cels[1].image, st)
    spr:saveAs(spr.filename)
    spr:close()
  end
end
