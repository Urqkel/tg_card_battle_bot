from flask import Flask, request, send_file
import imageio, base64, io

app = Flask(__name__)

@app.route("/create_gif", methods=["POST"])
def create_gif():
    data = request.json
    frames = [imageio.imread(io.BytesIO(base64.b64decode(f.split(",")[1]))) for f in data["frames"]]
    gif_bytes = io.BytesIO()
    imageio.mimsave(gif_bytes, frames, format='GIF', duration=0.5)
    gif_bytes.seek(0)
    return send_file(gif_bytes, mimetype="image/gif", download_name="battle.gif")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
