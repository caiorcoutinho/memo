from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        # Permite que o Pillow abra fotos HEIC/HEIF (formato padrão do iPhone),
        # tanto para ler o EXIF quanto para convertê-las na hora do upload.
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except Exception:
            pass
