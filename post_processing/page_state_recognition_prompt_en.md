# Page and State Recognition Prompt

## Task Objective

Analyze an Android phone interface screenshot to identify the **page name** and **page state** using **only this single screenshot**. Ensure that processing the same screenshot multiple times yields identical results. Do not compare or refer to other steps or images; describe only what is visible in this image.

## Output Format

You must strictly output in the following JSON format, without any additional content:

```json
{
  "page_name": "Page Name",
  "state_name": "Short State Label",
  "state_description": "One or two sentences describing the visible page state and content in this screenshot."
}
```

- **page_name**: Page name (see Page Recognition Rules below).
- **state_name**: Short state label (2–6 words) summarizing the main state type inferred from this image (e.g., input_empty, recording, list_loaded).
- **state_description**: One or two **self-contained** sentences describing **only what is visible in this screenshot** (page state and content). Do not refer to other steps or comparisons.

## Page Recognition Rules

### 1. Page Naming Convention

**Format**: `[App Name]-[Page Type]`

**Rules**:
- App Name: Use the app’s Chinese or English name (based on what is actually shown in the screenshot)
  - Examples: WeChat, 微信, Contacts, AudioRecorder
- Page Type: Describe the main function or content of the current interface
  - Examples: Main Page, Chat Page, Contact List, Recording Page, Settings Page

**Complete Examples**:
- `WeChat-Chat Page`
- `Contacts-Contact List`
- `AudioRecorder-Recording Page`
- `Settings-System Settings`
- `System-Home Screen`

### 2. Page Recognition Principles

1. **Main function**: Identify the primary function or content of the interface.
2. **App**: Identify the app from interface elements, icons, title bar.
3. **Page type**: List, detail, input, settings, main, etc., from layout and main elements.

### 3. Special Cases

- **System Home Screen**: `System-Home Screen`
- **Launch/Loading**: `[App Name]-Launch Page`
- **Error**: `[App Name]-Error Page`
- **Permission request / popup**: Identify as current page; describe the popup or permission text in state_description.

## State Recognition Rules (Based Only on This Screenshot)

### 1. state_description (Primary Field, Required)

**Definition**: One or two sentences describing the **visible page state and content in this screenshot**. Answer only from this image; do not infer or compare to other steps.

**Principles**:
- **Only this image**: Describe what is actually visible (list empty or not, selection, input content, keyboard, recording/playback, popup, title or key text).
- **Self-contained**: A reader should be able to imagine the interface from this text alone, without other screenshots or step context.
- **No comparison**: Do not use phrases like “different from previous step” or “compared to other steps.”

**Suggested dimensions** (use only what is visible in this screenshot):
- List: empty or not, number of items visible, selection, content type (e.g., contact list, settings list).
- Input: input field empty or filled, keyboard open or not.
- Chat/session: who the conversation is with, input and keyboard state.
- Recording/playback: recording, playing, paused, duration or waveform visible.
- Popup/permission: popup title or permission text.
- Error: error message or type (e.g., permission denied, network error).

**Examples** (all derivable from a single screenshot):
- `Contact list loaded, multiple entries visible, no selection.`
- `Chat interface, input field empty, keyboard not open.`
- `Chat interface, input field contains text, keyboard open.`
- `Recording screen, recording in progress, timer visible.`
- `Settings list with Wi-Fi, Bluetooth, etc. visible.`
- `Popup: permission request, text "Allow recording?".`
- `List empty, no contacts.`
- `Recording screen, paused, one recording file shown.`

### 2. state_name (Short Label, Required)

Summarize the state in **2–6 words** from this screenshot only. Must agree with state_description.

Common labels (non-exhaustive): `default`, `loading`, `empty`, `error`, `input_empty`, `input_filled`, `keyboard_open`, `recording`, `playing`, `paused`, `emoji_selected`, `menu_open`, `list_loaded`, `item_selected`, `permission_request`.

## Recognition Examples

### Example 1
**Screenshot**: Chat message list, bottom input field empty  
```json
{
  "page_name": "WeChat-Chat Page",
  "state_name": "input_empty",
  "state_description": "Chat interface, bottom input field visible and empty, keyboard not open."
}
```

### Example 2
**Screenshot**: Chat messages, input field has text, keyboard open  
```json
{
  "page_name": "WeChat-Chat Page",
  "state_name": "keyboard_open",
  "state_description": "Chat interface, input field has content, virtual keyboard open."
}
```

### Example 3
**Screenshot**: Contact list with multiple items  
```json
{
  "page_name": "Contacts-Contact List",
  "state_name": "list_loaded",
  "state_description": "Contact list loaded, multiple contacts visible, no selection."
}
```

### Example 4
**Screenshot**: Recording UI, record button highlighted, duration shown  
```json
{
  "page_name": "AudioRecorder-Recording Page",
  "state_name": "recording",
  "state_description": "Recording screen, recording in progress, timer displayed."
}
```

### Example 5
**Screenshot**: System home screen, app icon grid  
```json
{
  "page_name": "System-Home Screen",
  "state_name": "default",
  "state_description": "System home screen with app icon grid."
}
```

### Example 6
**Screenshot**: Settings page, options list  
```json
{
  "page_name": "Settings-System Settings",
  "state_name": "list_loaded",
  "state_description": "Settings page, list loaded with multiple options visible."
}
```

### Example 7
**Screenshot**: Permission request dialog  
```json
{
  "page_name": "AudioRecorder-Recording Page",
  "state_name": "permission_request",
  "state_description": "Recording app with permission request dialog in foreground, asking to allow recording."
}
```

## Consistency

- **Same screenshot, multiple runs**: Should yield the same or semantically equivalent page_name, state_name, and state_description.
- **Base only on this image**: Do not refer to or infer other steps or screenshots.
- **Stable naming**: Use consistent app and page-type terms.

## Edge Cases

- **Cannot identify page**:
  ```json
  {
    "page_name": "Unknown-Unknown Page",
    "state_name": "unknown",
    "state_description": "Interface content could not be clearly identified."
  }
  ```
- **Page clear, state hard to label**: Write state_description from the image; use `default` or the closest label for state_name.

## Output Requirements

1. **Strict JSON**: Output only JSON; no explanation or markdown code fence.
2. **All three fields required**: `page_name`, `state_name`, and `state_description` must be non-empty.
3. **state_description**: One or two sentences; describe only what is visible in this screenshot; do not invent comparisons to other steps.

**Correct output example**:
```json
{"page_name": "WeChat-Chat Page", "state_name": "input_empty", "state_description": "Chat interface, bottom input field empty, keyboard not open."}
```

---

**Start recognition**: Using only the one screenshot provided, output the JSON above.
