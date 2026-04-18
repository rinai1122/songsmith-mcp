-- songsmith_helper.lua
--
-- Companion ReaScript for Songsmith MCP. Performs a few ops that are
-- awkward through python-reapy:
--
--   * Insert a MIDI file at the edit cursor on the currently selected track.
--   * Write a region with a colour and a name.
--   * Convert the current MIDI item's lyric meta-events into REAPER's
--     built-in lyrics (Track → Notes pane) so the user can sing along.
--
-- Invocation: the Python bridge shells to REAPER via ReaScript's file-based
-- command queue. Drop this in your REAPER ResourcePath/Scripts/ folder and
-- add the three actions to the action list.

function import_midi(path)
  reaper.InsertMedia(path, 0)
end

function add_region(start_time, end_time, name, color)
  reaper.AddProjectMarker2(0, true, start_time, end_time, name, -1, color or 0)
end

function lyrics_from_midi_events()
  local item = reaper.GetSelectedMediaItem(0, 0)
  if not item then return end
  local take = reaper.GetActiveTake(item)
  if not take or not reaper.TakeIsMIDI(take) then return end

  local _, _, text_cnt = reaper.MIDI_CountEvts(take)
  local collected = {}
  for i = 0, text_cnt - 1 do
    local ok, _, _, ppqpos, typ, msg = reaper.MIDI_GetTextSysexEvt(take, i)
    if ok and typ == 5 then  -- 5 = lyric event
      collected[#collected + 1] = msg
    end
  end
  local joined = table.concat(collected, " ")
  reaper.GetSetMediaItemInfo_String(item, "P_NOTES", joined, true)
end

-- Default entry-point: run lyrics_from_midi_events when this script is invoked
-- as an action.
lyrics_from_midi_events()
