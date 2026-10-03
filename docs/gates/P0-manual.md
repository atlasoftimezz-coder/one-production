# P0 manual check — Foundation

Tick each box. If one fails, write what you saw next to it and tell me.
Everything happens in the folder `D:\Projects\YT project\one-production\`.

- [ ] **1. It starts.** Double-click `start.bat`.
  A black window opens, and a browser tab shows **One Production**, Status **ok**,
  Version **0.0.1**, and a Data folder path.
  *If not:* send me `<data folder>\logs\app.log` (or a photo of the black window).

- [ ] **2. Data folder is real.** Open the Data path shown on the page in Explorer.
  It contains five folders: `projects`, `profiles`, `backups`, `logs`, `cache`.

- [ ] **3. It stops.** Close the black window, then refresh the browser tab.
  The page no longer loads (the app really stopped).

- [ ] **4. Two copies don't clash.** Double-click `start.bat` twice.
  Both open, the second one on a different port number in the address bar (8731 instead
  of 8730). Close both black windows afterwards.

- [ ] **5. Automatic check is green.** Open `docs\gates\P0-auto.md`.
  The bottom says **RESULT: PASS**. (Or run `check.bat P0` yourself and read the last line.)

- [ ] **6. (Optional) Move the data folder.** Create the file
  `%APPDATA%\OneProduction\settings.json` with this content:

  ```json
  { "data_dir": "D:\\OneProductionData" }
  ```

  Start again: the page shows `D:\OneProductionData` and the five folders appear there.
  Delete the file afterwards to go back to the default.

## Sign-off

When all boxes are ticked, open `docs\phase-log.md` and change the P0 row to
Manual **PASS**, Approved **yes** — or just tell me "P0 approved".
