import imageio
import os

def start_battle(player1, player2):
    """
    Simplified battle logic:
    - Pick card images from static/cards/
    - Generate GIF showing the fight
    """
    # Example: load images
    frames = []
    path1 = f"static/cards/{player1}.png"
    path2 = f"static/cards/{player2}.png"
    for frame in [path1, path2, path1]:
        frames.append(imageio.imread(frame))
    gif_path = f"static/battle_{player1}_vs_{player2}.gif"
    imageio.mimsave(gif_path, frames, duration=0.5)
    return gif_path
