from flask import Flask, render_template

app = Flask(__name__, template_folder='.', static_folder='.')

@app.route('/')
def home():
    # 'data' can be any HTML string you want to inject
    data = "<p>This is dynamic content injected by Flask!</p>"
    return render_template('index.html', data=data)

if __name__ == '__main__':
    app.run(debug=True)
