class FacialError(Exception):
    """Erreur de base pour le module de reconnaissance faciale."""
    pass


class NoFaceDetectedError(FacialError):
    """Aucun visage détecté dans l'image."""
    pass


class MultipleFacesError(FacialError):
    """Plusieurs visages détectés — ambiguïté pour l'extraction d'embedding."""
    pass


class InvalidImageError(FacialError):
    """Image invalide (format non supporté ou corrompue)."""
    pass


class ImageTooLargeError(FacialError):
    """Image dépasse la taille maximale autorisée."""
    pass
