/**
 * Self-contained webpack config for the 3DX KnowledgeForge widget.
 *
 * The official `@widget-lab/widget-templates-webpack-configs` package
 * provides shared `dev` / `devS3` / `prod` configs but lives on the
 * private 3DS GitLab npm registry — see `.npmrc` for context. This
 * config bakes in the same essentials (XHTML entry copy, babel-loader
 * for TS/JSX, HTTPS dev server on 8081 with the `/widget` path) so
 * the widget can be built on any machine without registry access.
 */
const path = require("path");
const CopyPlugin = require("copy-webpack-plugin");

module.exports = (_env, argv) => {
  const isProd = argv.mode === "production";

  return {
    entry: path.resolve(__dirname, "src/index.tsx"),
    output: {
      path: path.resolve(__dirname, "dist"),
      filename: "main.js",
      clean: true,
    },
    devtool: isProd ? false : "source-map",
    resolve: {
      extensions: [".tsx", ".ts", ".jsx", ".js"],
      // Force module lookups to start from this app's ``node_modules``
      // and fall back to the standard ``node_modules`` resolution.
      // ``apps/_shared/`` has no node_modules of its own (#83 slice 3
      // pulled React-using code in there), so without this webpack
      // can't resolve ``react/jsx-runtime`` / ``@widget-lab/*`` when
      // bundling files from the shared package.
      modules: [path.resolve(__dirname, "node_modules"), "node_modules"],
    },
    module: {
      rules: [
        {
          test: /\.css$/,
          use: ["style-loader", "css-loader"],
        },
        {
          test: /\.(ts|tsx|js|jsx)$/,
          // Transpile our source AND the local 3ddashboard-utils dep
          // (it's already compiled, but babel handles ESM ↔ CJS interop).
          exclude: /node_modules\/(?!@widget-lab\/)/,
          use: {
            loader: "babel-loader",
            options: {
              presets: [
                ["@babel/preset-env", { targets: { esmodules: true } }],
                ["@babel/preset-react", { runtime: "automatic" }],
                "@babel/preset-typescript",
              ],
            },
          },
        },
      ],
    },
    plugins: [
      // The XHTML entry is hand-written and must NOT have webpack inject
      // a <script> tag — its own bootstrap derives `main.js` from
      // `widget.uwaUrl`. We copy it verbatim.
      new CopyPlugin({
        patterns: [
          { from: path.resolve(__dirname, "src/index.html"), to: "index.html" },
        ],
      }),
    ],
    devServer: {
      server: "https",
      port: 8081,
      open: ["/widget"],
      hot: true,
      static: false,
      historyApiFallback: false,
      devMiddleware: {
        // Serve the bundle under /widget/ so it matches the URL the
        // 3DDashboard host uses for registered tiles.
        publicPath: "/widget/",
        writeToDisk: false,
      },
      client: {
        overlay: { runtimeErrors: false },
      },
    },
    performance: {
      hints: false,
    },
  };
};
