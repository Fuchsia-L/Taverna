import { AttachedImage } from "@/types/chat";

const DEFAULT_MAX_DIMENSION = 1920;
const DEFAULT_MAX_BYTES = 1_000_000;
const DEFAULT_QUALITIES = [0.85, 0.7, 0.5];

type CompressOptions = {
  maxDimension?: number;
  maxBytes?: number;
  qualities?: number[];
};

function loadImageFromFile(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(objectUrl);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Failed to load image"));
    };
    image.src = objectUrl;
  });
}

function canvasToBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          reject(new Error("Image compression failed"));
          return;
        }
        resolve(blob);
      },
      "image/jpeg",
      quality
    );
  });
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Failed to read compressed image"));
    reader.readAsDataURL(blob);
  });
}

export async function compressImage(file: File, opts?: CompressOptions): Promise<AttachedImage> {
  if (!file.type.startsWith("image/")) {
    throw new Error("Only image files are supported");
  }

  const maxDimension = opts?.maxDimension ?? DEFAULT_MAX_DIMENSION;
  const maxBytes = opts?.maxBytes ?? DEFAULT_MAX_BYTES;
  const qualities = opts?.qualities?.length ? opts.qualities : DEFAULT_QUALITIES;
  const image = await loadImageFromFile(file);

  const longest = Math.max(image.width, image.height);
  const scale = longest > maxDimension ? maxDimension / longest : 1;
  const width = Math.max(1, Math.round(image.width * scale));
  const height = Math.max(1, Math.round(image.height * scale));

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) {
    throw new Error("Failed to initialize image canvas");
  }
  context.drawImage(image, 0, 0, width, height);

  let selectedBlob: Blob | null = null;
  for (const quality of qualities) {
    const blob = await canvasToBlob(canvas, quality);
    selectedBlob = blob;
    if (blob.size <= maxBytes) {
      break;
    }
  }

  if (!selectedBlob) {
    throw new Error("Image compression failed");
  }

  const dataUrl = await blobToDataUrl(selectedBlob);
  const nameWithoutExt = file.name.replace(/\.[^.]+$/, "");
  return {
    dataUrl,
    mimeType: "image/jpeg",
    name: `${nameWithoutExt}.jpg`,
    sizeBytes: selectedBlob.size,
  };
}
