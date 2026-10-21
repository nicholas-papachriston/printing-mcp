"""Stand-in for hy3dgen.rembg: returns the image with an alpha channel."""


class BackgroundRemover:
    def __call__(self, image):
        return image.convert("RGBA")
