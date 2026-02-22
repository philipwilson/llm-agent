---
name: imagegen
description: Generate images using Gemini image generation
argument-hint: <prompt> [-m pro] [-a 16:9] [-n 2]
---
Run the gemini-imagegen CLI to generate an image. Build the command from the arguments below, then execute it.

Prompt: $ARGUMENTS

Command template:
  gemini-imagegen [options] "<prompt>"

Options to consider:
  -m flash|pro       Model (flash is fast default, pro is higher quality)
  -a RATIO           Aspect ratio: 1:1 (default), 16:9, 9:16, 3:2, 2:3, 4:3, 3:4, 4:5, 5:4, 21:9
  -n N               Number of images to generate (default 1)
  -t TEMP            Temperature 0.0-2.0 (higher = more creative)
  --format png|webp  Output format (default png)
  -o DIR             Output directory (default: output/)

Parse the user's arguments to decide which options to use. If the user only provides a text description, just pass it as the prompt with no extra flags. If they include flags like -m, -a, -n, etc., pass those through.

Run the command and report the result. If it succeeds, tell the user where the images were saved.
